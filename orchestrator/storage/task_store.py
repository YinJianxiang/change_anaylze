"""Task persistence boundary backed by the existing SQLite store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.mail_ingest import MessageStore
from orchestrator.time_utils import now_beijing

STATUSES = frozenset({'NEW','PROCESSING','DONE','FAILED'})
TRANSITIONS = {('NEW','PROCESSING'), ('PROCESSING','DONE'), ('PROCESSING','FAILED'), ('FAILED','NEW')}


class TaskStore:
    def __init__(self, database: Path) -> None:
        self._store = MessageStore(database)
        self.connection = self._store.connection
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, "
            "payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'NEW', retry_count INTEGER NOT NULL DEFAULT 0, "
            "last_error TEXT, result_payload TEXT, feedback TEXT, reviewer_name TEXT, "
            "dingtalk_user_id TEXT, routing_error TEXT, "
            "locked_at TEXT, locked_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)"
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(tasks)")}
        for name in ("reviewer_name", "dingtalk_user_id", "routing_error"):
            if name not in columns:
                self.connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT")
        self.connection.execute(
            "INSERT OR IGNORE INTO tasks(task_id,message_id,payload,status,retry_count,last_error,result_payload,feedback,"
            "reviewer_name,dingtalk_user_id,routing_error,"
            "locked_at,locked_by,created_at,updated_at,completed_at) "
            "SELECT task_id,message_id,payload,status,retry_count,last_error,result_payload,feedback,"
            "reviewer_name,dingtalk_user_id,routing_error,locked_at,locked_by,"
            "COALESCE(created_at,processed_at),COALESCE(updated_at,processed_at),completed_at FROM processed_messages"
        )
        self.connection.execute("DROP TRIGGER IF EXISTS sync_legacy_task_insert")
        self.connection.execute(
            "CREATE TRIGGER sync_legacy_task_insert AFTER INSERT ON processed_messages BEGIN "
            "INSERT OR IGNORE INTO tasks(task_id,message_id,payload,status,retry_count,created_at,updated_at,"
            "reviewer_name,dingtalk_user_id,routing_error) "
            "VALUES(COALESCE(NEW.task_id,NEW.message_id),NEW.message_id,NEW.payload,NEW.status,NEW.retry_count,"
            "COALESCE(NEW.created_at,NEW.processed_at),COALESCE(NEW.updated_at,NEW.processed_at),"
            "NEW.reviewer_name,NEW.dingtalk_user_id,NEW.routing_error); END"
        )
        self.connection.commit()

    def claim_new(self, worker_id: str, limit: int | None = None) -> list[tuple[str, dict[str, Any], int]]:
        query = "SELECT task_id,payload,retry_count FROM tasks WHERE status='NEW' ORDER BY created_at,task_id"
        rows = self.connection.execute(query + (" LIMIT ?" if limit else ""), ((limit,) if limit else ())).fetchall()
        claimed: list[tuple[str, dict[str, Any], int]] = []
        for task_id, payload, retry_count in rows:
            now = now_beijing()
            cursor = self.connection.execute(
                "UPDATE tasks SET status='PROCESSING',updated_at=?,locked_at=?,locked_by=?,last_error=NULL WHERE task_id=? AND status='NEW'",
                (now, now, worker_id, task_id),
            )
            if cursor.rowcount == 1:
                claimed.append((task_id, json.loads(payload), retry_count))
        self.connection.commit()
        return claimed

    def claim(self, task_id: str, worker_id: str) -> tuple[str, dict[str, Any], int] | None:
        now = now_beijing()
        cursor = self.connection.execute(
            "UPDATE tasks SET status='PROCESSING',updated_at=?,locked_at=?,"
            "locked_by=?,last_error=NULL WHERE task_id=? AND status='NEW'", (now, now, worker_id, task_id))
        if cursor.rowcount != 1:
            self.connection.commit()
            return None
        row = self.connection.execute("SELECT task_id,payload,retry_count FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        self.connection.commit()
        return (row[0], json.loads(row[1]), row[2]) if row else None

    def recover_stale_tasks(self, timeout_seconds: int = 900) -> int:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        cursor = self.connection.execute("UPDATE tasks SET status='NEW', locked_at=NULL, locked_by=NULL, updated_at=? WHERE status='PROCESSING' AND locked_at IS NOT NULL AND julianday(locked_at) <= julianday(?, ?)", (now_beijing(), now_beijing(), f'-{int(timeout_seconds)} seconds'))
        self.connection.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._store.close()

    def transition(self, task_id: str, expected: str, target: str, *, error: str | None = None,
                   feedback: str | None = None, result_payload: dict[str, Any] | None = None,
                   max_retries: int = 3) -> bool:
        if expected not in STATUSES or target not in STATUSES:
            raise ValueError("Unsupported task status")
        if (expected, target) not in TRANSITIONS:
            return False
        cursor = self.connection.execute(
            "UPDATE tasks SET status=?, last_error=?, feedback=COALESCE(?,feedback), "
            "result_payload=COALESCE(?,result_payload), "
            "retry_count=retry_count+CASE WHEN ?='FAILED' THEN 1 ELSE 0 END, updated_at=?, "
            "locked_at=NULL, locked_by=NULL, completed_at=CASE WHEN ?='DONE' THEN ? ELSE completed_at END "
            "WHERE task_id=? AND status=? AND NOT (?='NEW' AND retry_count>=?)",
            (target, error, feedback, json.dumps(result_payload, ensure_ascii=False) if result_payload is not None else None,
             target, now_beijing(), target, now_beijing(), task_id, expected, target, max_retries),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        columns = [item[1] for item in self.connection.execute("PRAGMA table_info(tasks)")]
        return dict(zip(columns, row))
