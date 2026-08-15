from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.mail_process import read_payloads  # noqa: E402


class MailProcessTests(unittest.TestCase):
    def test_previews_new_payload_without_changing_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE processed_messages "
                "(message_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            payload = {
                "message_id": "<mail@example.test>",
                "projects": [{"project": "market-admin", "branch": "feature-1"}],
                "requirement_urls": ["https://docs.example.test/requirement"],
                "body_text": "request body",
            }
            connection.execute(
                "INSERT INTO processed_messages VALUES (?, ?, ?)",
                (payload["message_id"], "2026-08-13T10:00:00+00:00", json.dumps(payload)),
            )
            connection.commit()
            connection.close()

            self.assertEqual(
                read_payloads(database),
                [{
                    "message_id": "<mail@example.test>",
                    "projects": [{"project": "market-admin", "branch": "feature-1"}],
                    "reference_documents": ["https://docs.example.test/requirement"],
                }],
            )

            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.total_changes, 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM processed_messages").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute("SELECT status FROM processed_messages").fetchone()[0],
                    "NEW",
                )
            finally:
                connection.close()

    def test_reparses_projects_from_stored_body_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE processed_messages "
                "(message_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            payload = {
                "message_id": "<mail@example.test>",
                "projects": [],
                "requirement_urls": [],
                "body_text": (
                    "工程|分支|版本号:\n"
                    "market-admin | dev_20260811_jqt_datacontrol45\n"
                    "market-job | dev_20260811_jqt_datacontrol45\n"
                    "工程依赖:\n"
                ),
            }
            connection.execute(
                "INSERT INTO processed_messages VALUES (?, ?, ?)",
                (payload["message_id"], "2026-08-13T10:00:00+00:00", json.dumps(payload)),
            )
            connection.commit()
            connection.close()

            result = read_payloads(database)
            self.assertEqual(result[0]["projects"][0]["project"], "market-admin")
            self.assertEqual(result[0]["projects"][0]["branch"], "dev_20260811_jqt_datacontrol45")


if __name__ == "__main__":
    unittest.main()
