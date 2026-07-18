#!/usr/bin/env python3
"""Validate authenticated database unlock and rejection paths."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from omics_unlock import OmicsUnlockError, unlock_omics_database  # noqa: E402


APP_DIR = ROOT / "app_source"
KEY_FILE = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/secrets/omics_key.b64")
MANIFEST = APP_DIR / "data/lab_omics/wulab_omics_v1.sqlite.zlib.aesctr.manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class OmicsUnlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not KEY_FILE.is_file() or not MANIFEST.is_file():
            raise unittest.SkipTest("encrypted test material is unavailable")
        cls.key = KEY_FILE.read_text(encoding="ascii").strip()
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        os.environ.pop("MY_BIO_TOOLS_OMICS_DB", None)
        os.environ.pop("MY_BIO_TOOLS_OMICS_KEY_B64", None)
        os.environ.pop("MY_BIO_TOOLS_OMICS_UNLOCK_DIR", None)

    def test_valid_key_unlocks_read_only_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omics-unlock-test-") as temporary:
            os.environ["MY_BIO_TOOLS_OMICS_KEY_B64"] = self.key
            os.environ["MY_BIO_TOOLS_OMICS_UNLOCK_DIR"] = temporary
            database = unlock_omics_database(APP_DIR)
            self.assertEqual(sha256_file(database), self.manifest["plaintext_sha256"])
            self.assertEqual(database.stat().st_mode & 0o777, 0o400)
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM datasets WHERE inclusion_status='included'").fetchone()[0],
                    16,
                )
            finally:
                connection.close()

    def test_wrong_or_missing_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="omics-unlock-reject-") as temporary:
            os.environ["MY_BIO_TOOLS_OMICS_KEY_B64"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            os.environ["MY_BIO_TOOLS_OMICS_UNLOCK_DIR"] = temporary
            with self.assertRaises(OmicsUnlockError):
                unlock_omics_database(APP_DIR)
            self.assertFalse((Path(temporary) / "wulab_omics_v1.sqlite").exists())
        os.environ.pop("MY_BIO_TOOLS_OMICS_KEY_B64", None)
        with tempfile.TemporaryDirectory(prefix="omics-unlock-missing-") as temporary:
            os.environ["MY_BIO_TOOLS_OMICS_UNLOCK_DIR"] = temporary
            with self.assertRaises(OmicsUnlockError):
                unlock_omics_database(APP_DIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
