"""Publish a verified Feishu card action for the task worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestrator.messaging.events import make_event
from orchestrator.messaging.rabbitmq import RabbitMQ


def main() -> int:
    parser = argparse.ArgumentParser(description="Handle a Feishu confirmation callback payload.")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--payload", required=True, help="JSON with message_id, action, and optional feedback")
    args = parser.parse_args()
    payload = json.loads(args.payload)
    task_id = payload.get("task_id") or payload.get("message_id")
    event = make_event(task_id, "feishu.confirmation.received", {
        "action": payload["action"], "feedback": payload.get("feedback", ""),
        "open_id": payload.get("open_id"), "chat_id": payload.get("chat_id"),
    })
    broker = RabbitMQ()
    try:
        broker.publish("feishu.confirmation.received", event)
    finally:
        broker.close()
    print(json.dumps({"published": True, "event_id": event["event_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
