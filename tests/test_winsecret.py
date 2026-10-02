import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import winsecret
from winsecret import forget_key, load_key, save_key


class WinSecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = winsecret.DATA_ROOT
        winsecret.DATA_ROOT = Path(self.tmp.name)

    def tearDown(self):
        winsecret.DATA_ROOT = self.old
        self.tmp.cleanup()

    def test_roundtrip_and_forget(self):
        with patch("winsecret.protect", side_effect=lambda b: b"enc:" + b), \
             patch("winsecret.unprotect", side_effect=lambda b: b[4:]):
            self.assertIsNone(load_key())
            save_key("sk-test-123")
            self.assertEqual(load_key(), "sk-test-123")
            forget_key()
            self.assertIsNone(load_key())

    def test_stored_blob_is_not_plaintext(self):
        with patch("winsecret.protect", side_effect=lambda b: b"enc:" + b):
            save_key("sk-plain-canary")
        content = (winsecret.DATA_ROOT / winsecret.SECRET_FILE).read_text("ascii")
        self.assertNotIn("sk-plain-canary", content)
        try:
            json.loads(content)
            self.fail("stored payload should be base64, not raw json")
        except ValueError:
            pass
        # 结构应是 base64 可解码
        base64.b64decode(content)

    def test_corrupt_blob_returns_none(self):
        (winsecret.DATA_ROOT / winsecret.SECRET_FILE).write_text("!!!not-base64!!!", "ascii")
        self.assertIsNone(load_key())
