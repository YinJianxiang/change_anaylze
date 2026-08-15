from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from orchestrator.models.task import EventType, TaskEvent
from orchestrator.time_utils import now_beijing


class OutboxStore:
    """Durable events awaiting publication to the message broker."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=30)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS outbox_events (event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
            "event_type TEXT NOT NULL, event_payload TEXT NOT NULL, created_at TEXT NOT NULL, "
            "published_at TEXT, publish_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(published_at,created_at)"
        )
        self.connection.commit()

    def enqueue(self, event: TaskEvent | dict[str, Any]) -> bool:
        item = event.to_dict() if isinstance(event, TaskEvent) else event
        required = {"event_id", "task_id", "event_type", "created_at", "payload"}
        if not required.issubset(item):
            raise ValueError(f"outbox event missing fields: {sorted(required - item.keys())}")
        EventType(str(item["event_type"]))
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO outbox_events(event_id,task_id,event_type,event_payload,created_at) VALUES (?,?,?,?,?)",
            (item["event_id"], item["task_id"], item["event_type"],
             json.dumps(item["payload"], ensure_ascii=False), item["created_at"]),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        rows = self.connection.execute(
            "SELECT event_id,task_id,event_type,event_payload,created_at,publish_attempts,last_error "
            "FROM outbox_events WHERE published_at IS NULL ORDER BY rowid LIMIT ?", (limit,)
        ).fetchall()
        return [{"event_id": row[0], "task_id": row[1], "event_type": row[2],
                 "payload": json.loads(row[3]), "created_at": row[4],
                 "publish_attempts": row[5], "last_error": row[6]} for row in rows]

    def mark_published(self, event_id: str) -> bool:
        cursor = self.connection.execute(
            "UPDATE outbox_events SET published_at=?,publish_attempts=publish_attempts+1,last_error=NULL "
            "WHERE event_id=? AND published_at IS NULL", (now_beijing(), event_id)
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def mark_failed(self, event_id: str, error: str) -> bool:
        cursor = self.connection.execute(
            "UPDATE outbox_events SET publish_attempts=publish_attempts+1,last_error=? "
            "WHERE event_id=? AND published_at IS NULL", (error, event_id)
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()
