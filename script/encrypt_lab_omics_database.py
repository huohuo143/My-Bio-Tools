#!/usr/bin/env python3
"""Compress and encrypt the read-only Wu Lab omics SQLite database."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import tempfile
import zlib


MAGIC = b"MBTO2"
DEFAULT_INPUT = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/wulab_omics_v1.sqlite")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "app_source/data/lab_omics/wulab_omics_v1.sqlite.zlib.aesctr"
DEFAULT_KEY_FILE = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/secrets/omics_key.b64")


def derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    encryption_key = hmac.new(master_key, b"My Bio Tools omics encryption", hashlib.sha256).digest()
    authentication_key = hmac.new(master_key, b"My Bio Tools omics authentication", hashlib.sha256).digest()
    return encryption_key, authentication_key


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compress_database(source: Path, target: Path) -> None:
    compressor = zlib.compressobj(level=9)
    with source.open("rb") as reader, target.open("wb") as writer:
        for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
            writer.write(compressor.compress(block))
        writer.write(compressor.flush())


def encrypt_file(source: Path, target: Path, master_key: bytes, original_size: int) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl is required for the dependency-free macOS encryption format")
    encryption_key, authentication_key = derive_keys(master_key)
    initialization_vector = secrets.token_bytes(16)
    header = MAGIC + struct.pack(">Q", original_size) + initialization_vector
    with tempfile.NamedTemporaryFile(prefix="omics-cipher-", dir=target.parent, delete=False) as temporary:
        ciphertext_path = Path(temporary.name)
    try:
        completed = subprocess.run(
            [
                openssl, "enc", "-aes-256-ctr",
                "-K", encryption_key.hex(), "-iv", initialization_vector.hex(),
                "-in", str(source), "-out", str(ciphertext_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "openssl encryption failed")
        authenticator = hmac.new(authentication_key, header, hashlib.sha256)
        with target.open("wb") as writer, ciphertext_path.open("rb") as reader:
            writer.write(header)
            for block in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                writer.write(block)
                authenticator.update(block)
            writer.write(authenticator.digest())
        os.chmod(target, 0o600)
    finally:
        ciphertext_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    for path in (args.output, args.key_file):
        if path.exists() and not args.force:
            raise FileExistsError(f"Refusing to overwrite without --force: {path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.key_file.parent.mkdir(parents=True, exist_ok=True)
    master_key = secrets.token_bytes(32)
    with tempfile.NamedTemporaryFile(prefix="omics-compressed-", dir=args.output.parent, delete=False) as temporary:
        compressed_path = Path(temporary.name)
    try:
        compress_database(args.input, compressed_path)
        encrypt_file(compressed_path, args.output, master_key, args.input.stat().st_size)
    finally:
        compressed_path.unlink(missing_ok=True)
    args.key_file.write_text(base64.b64encode(master_key).decode("ascii") + "\n", encoding="ascii")
    os.chmod(args.key_file, 0o600)
    manifest = args.output.with_suffix(args.output.suffix + ".manifest.json")
    manifest.write_text(
        "{\n"
        f'  "format": "MBTO2",\n'
        f'  "cipher": "AES-256-CTR",\n'
        f'  "authentication": "HMAC-SHA256 encrypt-then-MAC",\n'
        f'  "compression": "zlib level 9",\n'
        f'  "plaintext_size": {args.input.stat().st_size},\n'
        f'  "encrypted_size": {args.output.stat().st_size},\n'
        f'  "plaintext_sha256": "{sha256_file(args.input)}",\n'
        f'  "encrypted_sha256": "{sha256_file(args.output)}"\n'
        "}\n",
        encoding="utf-8",
    )
    print(f"Encrypted database: {args.output}")
    print(f"Build key (chmod 600): {args.key_file}")
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
