"""Persistent hand-off state between mailbox ingestion and the DingTalk Agent."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from orchestrator.time_utils import now_beijing


class AgentTaskStore:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS agent_tasks ("
            "task_id TEXT PRIMARY KEY, reviewer_name TEXT NOT NULL, dingtalk_user_id TEXT NOT NULL, "
            "access_token_hash TEXT NOT NULL, context_payload TEXT NOT NULL, status TEXT NOT NULL, "
            "result_payload TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)"
        )
        self.connection.commit()

    def create(self, task_id: str, reviewer_name: str, dingtalk_user_id: str,
               context: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        now = now_beijing()
        self.connection.execute(
            "INSERT OR REPLACE INTO agent_tasks(task_id,reviewer_name,dingtalk_user_id,access_token_hash,"
            "context_payload,status,created_at,updated_at) VALUES(?,?,?,?,?,'READY',?,?)",
            (task_id, reviewer_name, dingtalk_user_id, self._hash(token),
             json.dumps(context, ensure_ascii=False), now, now),
        )
        self.connection.commit()
        return token

    def get_context(self, task_id: str, token: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT context_payload,status,reviewer_name,dingtalk_user_id FROM agent_tasks "
            "WHERE task_id=? AND access_token_hash=? AND status IN ('READY','IN_PROGRESS')",
            (task_id, self._hash(token)),
        ).fetchone()
        if row is None:
            return None
        self.connection.execute(
            "UPDATE agent_tasks SET status=CASE WHEN status='READY' THEN 'IN_PROGRESS' ELSE status END,"
            "updated_at=? WHERE task_id=?", (now_beijing(), task_id),
        )
        self.connection.commit()
        context = json.loads(row[0])
        context.update({"task_id": task_id, "status": row[1], "reviewer_name": row[2],
                        "dingtalk_user_id": row[3]})
        return context

    def complete(self, task_id: str, token: str, result: dict[str, Any]) -> bool:
        now = now_beijing()
        cursor = self.connection.execute(
            "UPDATE agent_tasks SET status='COMPLETED',result_payload=?,updated_at=?,completed_at=? "
            "WHERE task_id=? AND access_token_hash=? AND status IN ('READY','IN_PROGRESS')",
            (json.dumps(result, ensure_ascii=False), now, now, task_id, self._hash(token)),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
