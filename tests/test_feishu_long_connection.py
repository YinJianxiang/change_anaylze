from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.feishu_long_connection import MessageDeduplicator


class FeishuLongConnectionTests(unittest.TestCase):
    def test_message_is_claimed_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            deduplicator = MessageDeduplicator(Path(temp_dir) / "messages.sqlite3")
            try:
                self.assertTrue(deduplicator.claim("om_123"))
                self.assertFalse(deduplicator.claim("om_123"))
            finally:
                deduplicator.close()


if __name__ == "__main__":
    unittest.main()
