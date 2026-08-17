"""Read previously ingested mail records for the next processing stage."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from orchestrator.mail_ingest import MessageStore, _parse_projects


def read_payloads(database: Path, limit: int | None = None) -> list[dict[str, object]]:
    """Preview NEW records without changing their state."""
    migration_store = MessageStore(database)
    migration_store.close()
    connection = sqlite3.connect(database)
    try:
        query = "SELECT message_id, payload FROM processed_messages WHERE status = 'NEW' ORDER BY processed_at, message_id"
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = connection.execute(query, params).fetchall()
        result: list[dict[str, object]] = []
        for message_id, payload in rows:
            try:
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError("Stored mail payload must be a JSON object")
                projects = value.get("projects", [])
                if not projects and isinstance(value.get("body_text"), str):
                    projects = [item.__dict__ for item in _parse_projects(value["body_text"])]
                requirement_urls = value.get("requirement_urls", [])
                item = {
                        "message_id": value.get("message_id", message_id),
                        "projects": projects if isinstance(projects, list) else [],
                        "reference_documents": requirement_urls if isinstance(requirement_urls, list) else [],
                }
                for key in ("reviewer_name", "dingtalk_user_id", "routing_error"):
                    if value.get(key):
                        item[key] = value[key]
                result.append(item)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read ingested mail data from SQLite.")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be greater than zero")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(read_payloads(args.database, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
