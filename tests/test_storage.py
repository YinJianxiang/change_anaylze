from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from orchestrator.mail_ingest import MessageStore
from orchestrator.storage.event_store import EventStore
from orchestrator.storage.task_store import TaskStore
from orchestrator.messaging.events import make_event
from orchestrator.messaging.events import make_event
from orchestrator.storage.outbox_store import OutboxStore


class StorageTests(unittest.TestCase):
    def test_new_event_uses_beijing_timezone(self) -> None:
        self.assertTrue(make_event("t1", "mail.task.created", {})["created_at"].endswith("+08:00"))
    def test_old_database_is_migrated_and_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "old.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE processed_messages (message_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO processed_messages VALUES ('m1', '2026-08-14T00:00:00Z', ?)", (json.dumps({"message_id": "m1"}),))
            connection.commit()
            connection.close()
            store = MessageStore(database)
            columns = {row[1] for row in store.connection.execute("PRAGMA table_info(processed_messages)")}
            row = store.connection.execute("SELECT task_id,created_at,status FROM processed_messages WHERE message_id='m1'").fetchone()
            store.close()
            self.assertTrue({"task_id", "created_at", "dingtalk_user_id"}.issubset(columns))
            self.assertEqual(row, ("m1", "2026-08-14T00:00:00Z", "NEW"))

    def test_claim_and_transition_are_compare_and_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "tasks.sqlite3"
            store = MessageStore(database)
            store.connection.execute("INSERT INTO processed_messages(message_id,task_id,processed_at,payload,status,retry_count) VALUES ('m1','t1','now','{}','NEW',0)")
            store.connection.commit()
            store.close()
            tasks = TaskStore(database)
            self.assertEqual(len(tasks.claim_new("worker-1")), 1)
            self.assertEqual(tasks.claim_new("worker-2"), [])
            self.assertTrue(tasks.transition("t1", "PROCESSING", "DONE"))
            self.assertFalse(tasks.transition("t1", "PROCESSING", "FAILED"))
            tasks.close()

    def test_event_claim_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events = EventStore(Path(temp_dir) / "events.sqlite3")
            self.assertTrue(events.claim("e1", "mail.task.created"))
            self.assertFalse(events.claim("e1", "mail.task.created"))
            events.close()

    def test_tasks_table_is_backfilled_and_receives_legacy_inserts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "tasks.sqlite3"
            messages = MessageStore(database)
            messages.connection.execute("INSERT INTO processed_messages(message_id,task_id,processed_at,payload) VALUES ('m1','t1','now','{}')")
            messages.connection.commit()
            messages.close()
            tasks = TaskStore(database)
            self.assertEqual(tasks.connection.execute("SELECT task_id FROM tasks").fetchall(), [("t1",)])
            messages = MessageStore(database)
            messages.connection.execute("INSERT INTO processed_messages(message_id,task_id,processed_at,payload) VALUES ('m2','t2','now','{}')")
            messages.connection.commit()
            messages.close()
            self.assertEqual(tasks.connection.execute("SELECT task_id FROM tasks ORDER BY task_id").fetchall(), [("t1",), ("t2",)])
            tasks.close()

    def test_failed_retry_is_counted_and_capped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "tasks.sqlite3"
            messages = MessageStore(database)
            messages.connection.execute("INSERT INTO processed_messages(message_id,task_id,processed_at,payload) VALUES ('m1','t1','now','{}')")
            messages.connection.commit()
            messages.close()
            tasks = TaskStore(database)
            for retry in range(3):
                self.assertEqual(len(tasks.claim_new("worker")), 1)
                self.assertTrue(tasks.transition("t1", "PROCESSING", "FAILED", error="temporary"))
                self.assertEqual(tasks.get("t1")["retry_count"], retry + 1)
                self.assertEqual(tasks.transition("t1", "FAILED", "NEW"), retry < 2)
            tasks.close()

    def test_recover_stale_processing_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "tasks.sqlite3"
            messages = MessageStore(database)
            messages.connection.execute("INSERT INTO processed_messages(message_id,task_id,processed_at,payload) VALUES ('m1','t1','now','{}')")
            messages.connection.commit()
            messages.close()
            tasks = TaskStore(database)
            tasks.claim_new("worker")
            tasks.connection.execute("UPDATE tasks SET locked_at='2000-01-01T00:00:00+00:00' WHERE task_id='t1'")
            tasks.connection.commit()
            self.assertEqual(tasks.recover_stale_tasks(60), 1)
            self.assertEqual(tasks.get("t1")["status"], "NEW")
            tasks.close()

    def test_event_model_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            make_event("t1", "unknown", {})

    def test_outbox_tracks_pending_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outbox = OutboxStore(Path(temp_dir) / "events.sqlite3")
            first = make_event("t1", "mail.task.created", {"value": "中文"})
            second = make_event("t2", "analysis.failed", {})
            self.assertTrue(outbox.enqueue(first))
            self.assertFalse(outbox.enqueue(first))
            self.assertTrue(outbox.enqueue(second))
            self.assertTrue(outbox.mark_failed(first["event_id"], "broker unavailable"))
            pending = outbox.pending()
            self.assertEqual([item["event_id"] for item in pending], [first["event_id"], second["event_id"]])
            self.assertEqual(pending[0]["publish_attempts"], 1)
            self.assertEqual(pending[0]["payload"], {"value": "中文"})
            self.assertTrue(outbox.mark_published(first["event_id"]))
            self.assertFalse(outbox.mark_published(first["event_id"]))
            self.assertEqual([item["event_id"] for item in outbox.pending()], [second["event_id"]])
            outbox.close()


if __name__ == "__main__":
    unittest.main()
