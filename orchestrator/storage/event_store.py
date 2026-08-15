from __future__ import annotations

import sqlite3
from pathlib import Path

from orchestrator.models.task import EventType
from orchestrator.time_utils import now_beijing


class EventStore:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=30)
        self.connection.execute("CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, processed_at TEXT NOT NULL)")
        self.connection.commit()

    def claim(self, event_id: str, event_type: str) -> bool:
        if not event_id:
            raise ValueError("event_id is required")
        EventType(event_type)
        cursor = self.connection.execute("INSERT OR IGNORE INTO processed_events(event_id,event_type,processed_at) VALUES (?,?,?)", (event_id, event_type, now_beijing()))
        self.connection.commit()
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()
