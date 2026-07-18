"""Unlock the authenticated, encrypted omics database after license validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib


MAGIC = b"MBTO2"
HEADER_SIZE = len(MAGIC) + 8 + 16
TAG_SIZE = 32


class OmicsUnlockError(RuntimeError):
    pass


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    encryption_key = hmac.new(master_key, b"My Bio Tools omics encryption", hashlib.sha256).digest()
    authentication_key = hmac.new(master_key, b"My Bio Tools omics authentication", hashlib.sha256).digest()
    return encryption_key, authentication_key


def _decode_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise OmicsUnlockError("omics key is invalid") from exc
    if len(key) != 32:
        raise OmicsUnlockError("omics key length is invalid")
    return key


def _verify_and_extract(encrypted_path: Path, ciphertext_path: Path, authentication_key: bytes) -> tuple[int, bytes]:
    size = encrypted_path.stat().st_size
    if size <= HEADER_SIZE + TAG_SIZE:
        raise OmicsUnlockError("encrypted omics database is truncated")
    with encrypted_path.open("rb") as reader:
        header = reader.read(HEADER_SIZE)
        if not header.startswith(MAGIC):
            raise OmicsUnlockError("encrypted omics database format is invalid")
        original_size = struct.unpack(">Q", header[len(MAGIC):len(MAGIC) + 8])[0]
        initialization_vector = header[-16:]
        ciphertext_size = size - HEADER_SIZE - TAG_SIZE
        authenticator = hmac.new(authentication_key, header, hashlib.sha256)
        with ciphertext_path.open("wb") as writer:
            remaining = ciphertext_size
            while remaining:
                block = reader.read(min(4 * 1024 * 1024, remaining))
                if not block:
                    raise OmicsUnlockError("encrypted omics database ended early")
                writer.write(block)
                authenticator.update(block)
                remaining -= len(block)
        expected = reader.read(TAG_SIZE)
    if not hmac.compare_digest(authenticator.digest(), expected):
        ciphertext_path.unlink(missing_ok=True)
        raise OmicsUnlockError("encrypted omics database authentication failed")
    return original_size, initialization_vector


def unlock_omics_database(app_dir: Path) -> Path:
    existing = os.environ.get("MY_BIO_TOOLS_OMICS_DB", "")
    if existing and Path(existing).is_file():
        return Path(existing)
    encrypted_path = app_dir / "data/lab_omics/wulab_omics_v1.sqlite.zlib.aesctr"
    if not encrypted_path.is_file():
        raise OmicsUnlockError("encrypted omics database is missing")
    master_key = _decode_key(os.environ.get("MY_BIO_TOOLS_OMICS_KEY_B64", ""))
    unlock_root_value = os.environ.get("MY_BIO_TOOLS_OMICS_UNLOCK_DIR", "")
    if not unlock_root_value:
        raise OmicsUnlockError("native omics unlock directory is missing")
    unlock_root = Path(unlock_root_value).expanduser().resolve()
    unlock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(unlock_root, 0o700)
    openssl = shutil.which("openssl")
    if not openssl:
        raise OmicsUnlockError("openssl is unavailable")
    encryption_key, authentication_key = _derive_keys(master_key)
    ciphertext_path = unlock_root / "omics.ciphertext"
    compressed_path = unlock_root / "omics.sqlite.zlib"
    database_path = unlock_root / "wulab_omics_v1.sqlite"
    original_size, initialization_vector = _verify_and_extract(encrypted_path, ciphertext_path, authentication_key)
    completed = subprocess.run(
        [
            openssl, "enc", "-d", "-aes-256-ctr",
            "-K", encryption_key.hex(), "-iv", initialization_vector.hex(),
            "-in", str(ciphertext_path), "-out", str(compressed_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    ciphertext_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        compressed_path.unlink(missing_ok=True)
        raise OmicsUnlockError(completed.stderr.strip() or "omics decryption failed")
    decompressor = zlib.decompressobj()
    with compressed_path.open("rb") as reader, database_path.open("wb") as writer:
        for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
            writer.write(decompressor.decompress(block))
        writer.write(decompressor.flush())
    compressed_path.unlink(missing_ok=True)
    if database_path.stat().st_size != original_size:
        database_path.unlink(missing_ok=True)
        raise OmicsUnlockError("unlocked omics database size is invalid")
    os.chmod(database_path, 0o400)
    os.environ["MY_BIO_TOOLS_OMICS_DB"] = str(database_path)
    return database_path


__all__ = ["OmicsUnlockError", "unlock_omics_database"]
