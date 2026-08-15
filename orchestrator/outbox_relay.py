"""Publish durable outbox events that previously failed delivery."""
from __future__ import annotations

import argparse
from pathlib import Path

from orchestrator.mail_ingest import DEFAULT_ENV_FILE, load_env_file
from orchestrator.messaging.rabbitmq import RabbitMQ
from orchestrator.storage.outbox_store import OutboxStore


def relay(database: Path, limit: int = 100) -> tuple[int, int]:
    outbox = OutboxStore(database)
    broker = RabbitMQ()
    published = failed = 0
    try:
        for event in outbox.pending(limit):
            try:
                broker.publish(event["event_type"], {key: event[key] for key in ("event_id", "task_id", "event_type", "created_at", "payload")})
                outbox.mark_published(event["event_id"])
                published += 1
            except Exception as error:
                outbox.mark_failed(event["event_id"], str(error))
                failed += 1
    finally:
        broker.close()
        outbox.close()
    return published, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Republish pending task events")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    load_env_file(args.env_file)
    published, failed = relay(args.database, args.limit)
    print(f"published={published} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
