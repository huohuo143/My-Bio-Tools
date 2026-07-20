#!/usr/bin/env python3
"""Validate the dependency-free backend Ed25519 verifier with RFC 8032."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from license_gate import LicenseValidationError, validate_license, verify_ed25519  # noqa: E402


def main() -> int:
    # RFC 8032 section 7.1, TEST 1: empty message.
    public_key = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a"
        "0ee172f3daa62325af021a68f707511a"
    )
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a"
        "84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46b"
        "d25bf5f0595bbe24655141438e7a100b"
    )
    assert verify_ed25519(public_key, b"", signature)
    tampered = bytearray(signature)
    tampered[0] ^= 1
    assert not verify_ed25519(public_key, b"", bytes(tampered))

    public_jwk = '{"kty":"OKP","crv":"Ed25519","x":"11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}'
    token = (
        "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9."
        "eyJ0eXAiOiJvZmZsaW5lLWxpY2Vuc2UiLCJzdWIiOiJ1c2VyLXRlc3QiLCJkZXZpY2UiOiJTSHJyVlVIZUtuNEZLdURoR2JZdVpMWlFaZV9JYTR4T1p3THcwcDFkRllBIiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjQxMDI0NDQ4MDAsInZlcnNpb24iOjEsIm9taWNzX2tleV9iNjQiOiJBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBPSJ9."
        "8GfIMRJgHTll-dziWVIVisx5UZfQoIyRKt7Maqkjm6-Iff52od2tzxpP9IVQYgpwCtC7fygBbrxqctITN6DHDg"
    )
    omics_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    claims = validate_license(
        token,
        public_jwk,
        "SHrrVUHeKn4FKuDhGbYuZLZQZe_Ia4xOZwLw0p1dFYA",
        now=1_800_000_000,
        omics_key_b64=omics_key,
    )
    assert claims["sub"] == "user-test"
    assert claims["omics_key_b64"] == omics_key
    try:
        validate_license(token, public_jwk, "wrong-device", now=1_800_000_000)
    except LicenseValidationError:
        pass
    else:
        raise AssertionError("wrong-device license was accepted")
    try:
        validate_license(
            token,
            public_jwk,
            "SHrrVUHeKn4FKuDhGbYuZLZQZe_Ia4xOZwLw0p1dFYA",
            now=1_800_000_000,
            omics_key_b64="AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
        )
    except LicenseValidationError:
        pass
    else:
        raise AssertionError("mismatched native omics key was accepted")
    print("Backend Ed25519, signed omics key, and device verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
