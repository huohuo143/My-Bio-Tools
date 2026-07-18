"""Minimal Ed25519 JWT verification for the frozen local backend.

The native shell remains the primary authorization boundary. This independent
gate prevents accidental or direct launcher use without adding a new binary
dependency to the bundled Python runtime.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


Q = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, Q - 2, Q)) % Q
I = pow(2, (Q - 1) // 4, Q)
IDENTITY = (0, 1, 1, 0)


class LicenseValidationError(RuntimeError):
    """Raised when the signed local authorization is unavailable or invalid."""


def _base64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise LicenseValidationError("invalid base64url value") from exc


def _recover_x(y: int) -> int:
    xx = (y * y - 1) * pow(D * y * y + 1, Q - 2, Q) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = x * I % Q
    if (x * x - xx) % Q != 0:
        raise LicenseValidationError("point is not on Ed25519 curve")
    return x


def _decode_point(encoded: bytes) -> tuple[int, int, int, int]:
    if len(encoded) != 32:
        raise LicenseValidationError("invalid Ed25519 point length")
    value = int.from_bytes(encoded, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= Q:
        raise LicenseValidationError("non-canonical Ed25519 point")
    x = _recover_x(y)
    if x & 1 != sign:
        x = Q - x
    point = (x, y, 1, x * y % Q)
    if _encode_point(point) != encoded:
        raise LicenseValidationError("non-canonical Ed25519 encoding")
    return point


def _encode_point(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = point
    inverse = pow(z, Q - 2, Q)
    affine_x = x * inverse % Q
    affine_y = y * inverse % Q
    value = affine_y | ((affine_x & 1) << 255)
    return value.to_bytes(32, "little")


def _add(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = first
    x2, y2, z2, t2 = second
    a = (y1 - x1) * (y2 - x2) % Q
    b = (y1 + x1) * (y2 + x2) % Q
    c = 2 * D * t1 * t2 % Q
    d_value = 2 * z1 * z2 % Q
    e = b - a
    f = d_value - c
    g = d_value + c
    h = b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _double(point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, z, _ = point
    a = x * x % Q
    b = y * y % Q
    c = 2 * z * z % Q
    d_value = -a % Q
    e = ((x + y) * (x + y) - a - b) % Q
    g = (d_value + b) % Q
    f = (g - c) % Q
    h = (d_value - b) % Q
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _scalar_multiply(
    point: tuple[int, int, int, int],
    scalar: int,
) -> tuple[int, int, int, int]:
    result = IDENTITY
    current = point
    while scalar:
        if scalar & 1:
            result = _add(result, current)
        current = _double(current)
        scalar >>= 1
    return result


def _points_equal(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    return (
        (first[0] * second[2] - second[0] * first[2]) % Q == 0
        and (first[1] * second[2] - second[1] * first[2]) % Q == 0
    )


BASE_Y = 4 * pow(5, Q - 2, Q) % Q
BASE_X = _recover_x(BASE_Y)
if BASE_X & 1:
    BASE_X = Q - BASE_X
BASE_POINT = (BASE_X, BASE_Y, 1, BASE_X * BASE_Y % Q)


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an Ed25519 signature and reject non-canonical/small-order points."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        public_point = _decode_point(public_key)
        r_point = _decode_point(signature[:32])
        scalar = int.from_bytes(signature[32:], "little")
        if scalar >= L:
            return False
        if not _points_equal(_scalar_multiply(public_point, L), IDENTITY):
            return False
        if not _points_equal(_scalar_multiply(r_point, L), IDENTITY):
            return False
        challenge = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(),
            "little",
        ) % L
        return _points_equal(
            _scalar_multiply(BASE_POINT, scalar),
            _add(r_point, _scalar_multiply(public_point, challenge)),
        )
    except LicenseValidationError:
        return False


def validate_license(
    token: str,
    public_jwk_json: str,
    installation_hash: str,
    *,
    now: int | None = None,
    omics_key_b64: str | None = None,
) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise LicenseValidationError("offline license is malformed")
    try:
        header = json.loads(_base64url_decode(parts[0]))
        claims = json.loads(_base64url_decode(parts[1]))
        public_jwk = json.loads(public_jwk_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LicenseValidationError("offline license JSON is invalid") from exc
    if header != {"alg": "EdDSA", "typ": "JWT"}:
        raise LicenseValidationError("offline license header is invalid")
    if public_jwk.get("kty") != "OKP" or public_jwk.get("crv") != "Ed25519":
        raise LicenseValidationError("offline license public key is invalid")
    public_key = _base64url_decode(str(public_jwk.get("x", "")))
    signature = _base64url_decode(parts[2])
    if not verify_ed25519(public_key, f"{parts[0]}.{parts[1]}".encode(), signature):
        raise LicenseValidationError("offline license signature is invalid")
    current_time = int(time.time()) if now is None else now
    if claims.get("typ") != "offline-license" or claims.get("version") != 1:
        raise LicenseValidationError("offline license type is invalid")
    claim_omics_key = claims.get("omics_key_b64")
    if not isinstance(claim_omics_key, str):
        raise LicenseValidationError("offline license omics key is missing")
    try:
        decoded_omics_key = base64.b64decode(claim_omics_key, validate=True)
    except Exception as exc:
        raise LicenseValidationError("offline license omics key is invalid") from exc
    if len(decoded_omics_key) != 32:
        raise LicenseValidationError("offline license omics key length is invalid")
    if omics_key_b64 is not None and not hmac.compare_digest(claim_omics_key, omics_key_b64):
        raise LicenseValidationError("native omics key does not match signed authorization")
    if claims.get("device") != installation_hash:
        raise LicenseValidationError("offline license belongs to another device")
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= current_time:
        raise LicenseValidationError("offline license has expired")
    if not isinstance(claims.get("iat"), int) or claims["iat"] > current_time + 300:
        raise LicenseValidationError("offline license issue time is invalid")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise LicenseValidationError("offline license subject is invalid")
    return claims


def require_license_from_environment() -> dict[str, Any]:
    token = os.environ.get("MY_BIO_TOOLS_OFFLINE_LICENSE", "")
    installation_hash = os.environ.get("MY_BIO_TOOLS_INSTALLATION_HASH", "")
    public_jwk = os.environ.get("MY_BIO_TOOLS_LICENSE_PUBLIC_JWK", "")
    omics_key_b64 = os.environ.get("MY_BIO_TOOLS_OMICS_KEY_B64", "")
    if not token or not installation_hash or not public_jwk or not omics_key_b64:
        raise LicenseValidationError("missing native authorization context")
    return validate_license(
        token,
        public_jwk,
        installation_hash,
        omics_key_b64=omics_key_b64,
    )
