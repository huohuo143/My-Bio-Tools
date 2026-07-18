#!/usr/bin/env python3
"""End-to-end smoke test for the frozen authorized omics backend."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import urlopen


DEFAULT_APP = Path(
    "/Volumes/FAFU/analysis_results/wulab_omics_app_v1/macOS_secure_candidate/dist/My Bio Tools.app"
)
TEST_PUBLIC_JWK = '{"kty":"OKP","crv":"Ed25519","x":"11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}'
TEST_INSTALLATION_HASH = "SHrrVUHeKn4FKuDhGbYuZLZQZe_Ia4xOZwLw0p1dFYA"
KEY_FILE = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/secrets/omics_key.b64")


def signed_test_license(omics_key: str) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required to create the ephemeral signed test license")
    script = r'''
const crypto = require("node:crypto");
const base64url = (value) => Buffer.from(value).toString("base64url");
const header = base64url(JSON.stringify({alg: "EdDSA", typ: "JWT"}));
const payload = base64url(JSON.stringify({
  typ: "offline-license", sub: "user-test",
  device: "SHrrVUHeKn4FKuDhGbYuZLZQZe_Ia4xOZwLw0p1dFYA",
  iat: 1700000000, exp: 4102444800, version: 1,
  omics_key_b64: process.env.TEST_OMICS_KEY_B64,
}));
const privateKey = crypto.createPrivateKey({
  key: {
    kty: "OKP", crv: "Ed25519",
    d: Buffer.from("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "hex").toString("base64url"),
    x: "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
  },
  format: "jwk",
});
const input = `${header}.${payload}`;
process.stdout.write(`${input}.${crypto.sign(null, Buffer.from(input), privateKey).toString("base64url")}`);
'''
    environment = os.environ.copy()
    environment["TEST_OMICS_KEY_B64"] = omics_key
    completed = subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError(completed.stderr.strip() or "unable to sign test license")
    return completed.stdout.strip()


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contents = args.app / "Contents"
    backend = contents / "Resources/backend/BioToolsBackend"
    app_dir = contents / "Resources/app_source"
    with (contents / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    if "MyBioToolsOmicsKeyB64" in info:
        raise RuntimeError("the app bundle must not contain the omics database key")
    if not backend.is_file() or not app_dir.is_dir() or not KEY_FILE.is_file():
        raise RuntimeError("packaged backend, app source, or external test key is missing")
    omics_key = KEY_FILE.read_text(encoding="ascii").strip()
    test_license = signed_test_license(omics_key)
    port = available_port()
    with tempfile.TemporaryDirectory(prefix="packaged-omics-smoke-") as temporary:
        temporary_path = Path(temporary)
        rejected_unlock = temporary_path / "rejected-unlock"
        rejected_environment = os.environ.copy()
        rejected_environment.pop("MY_BIO_TOOLS_OMICS_DB", None)
        rejected_environment.update(
            {
                "MY_BIO_TOOLS_OFFLINE_LICENSE": signed_test_license(
                    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                ),
                "MY_BIO_TOOLS_INSTALLATION_HASH": TEST_INSTALLATION_HASH,
                "MY_BIO_TOOLS_LICENSE_PUBLIC_JWK": TEST_PUBLIC_JWK,
                "MY_BIO_TOOLS_OMICS_KEY_B64": omics_key,
                "MY_BIO_TOOLS_OMICS_UNLOCK_DIR": str(rejected_unlock),
            }
        )
        rejected_log = temporary_path / "rejected.log"
        with rejected_log.open("wb") as log:
            rejected = subprocess.Popen(
                [
                    str(backend),
                    "--port", str(available_port()),
                    "--app-dir", str(app_dir),
                    "--parent-pid", str(os.getpid()),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=rejected_environment,
            )
            try:
                rejected.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                rejected.terminate()
                rejected.wait(timeout=5)
                raise RuntimeError("mismatched signed omics key was not rejected") from exc
        if rejected.returncode == 0 or (rejected_unlock / "wulab_omics_v1.sqlite").exists():
            raise RuntimeError("mismatched signed omics key reached the database unlock path")

        unlock_directory = temporary_path / "unlock"
        log_path = temporary_path / "backend.log"
        environment = os.environ.copy()
        environment.pop("MY_BIO_TOOLS_OMICS_DB", None)
        environment.update(
            {
                "PYTHONNOUSERSITE": "1",
                "MY_BIO_TOOLS_OFFLINE_LICENSE": test_license,
                "MY_BIO_TOOLS_INSTALLATION_HASH": TEST_INSTALLATION_HASH,
                "MY_BIO_TOOLS_LICENSE_PUBLIC_JWK": TEST_PUBLIC_JWK,
                "MY_BIO_TOOLS_OMICS_KEY_B64": omics_key,
                "MY_BIO_TOOLS_OMICS_UNLOCK_DIR": str(unlock_directory),
                "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
                "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
            }
        )
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(backend),
                    "--port", str(port),
                    "--app-dir", str(app_dir),
                    "--parent-pid", str(os.getpid()),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                health_url = f"http://127.0.0.1:{port}/_stcore/health"
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urlopen(health_url, timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("frozen backend health check timed out")
                if process.poll() is not None:
                    raise RuntimeError("frozen backend exited before health check")
                database = unlock_directory / "wulab_omics_v1.sqlite"
                connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                try:
                    included = connection.execute(
                        "SELECT COUNT(*) FROM datasets WHERE inclusion_status='included'"
                    ).fetchone()[0]
                    if included != 16:
                        raise AssertionError(f"expected 16 included datasets, found {included}")
                finally:
                    connection.close()
            except Exception as exc:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                details = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(f"{exc}\nBackend log:\n{details}") from exc
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    print("Frozen backend authorization, omics unlock, SQLite query, and health check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
