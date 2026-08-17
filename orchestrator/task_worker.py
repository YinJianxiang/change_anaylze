"""Route mailbox tasks to the configured DingTalk enterprise Agent."""
from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from pathlib import Path

from orchestrator.mail_ingest import DEFAULT_ENV_FILE, load_env_file
from orchestrator.messaging.events import make_event
from orchestrator.messaging.rabbitmq import RabbitMQ
from orchestrator.services.dingtalk_agent_service import DingTalkAgentService
from orchestrator.storage.agent_task_store import AgentTaskStore
from orchestrator.storage.event_store import EventStore
from orchestrator.storage.outbox_store import OutboxStore
from orchestrator.storage.task_store import TaskStore

MAX_RETRIES = 3
LOGGER = logging.getLogger(__name__)


class EventTaskWorker:
    def __init__(self, database: Path, broker: RabbitMQ) -> None:
        self.database = database
        self.broker = broker
        self.worker_id = f"task-worker-{uuid.uuid4().hex}"
        self.tasks: TaskStore | None = None
        self.events: EventStore | None = None
        self.outbox: OutboxStore | None = None
        self.agent_tasks: AgentTaskStore | None = None
        self.dingtalk = DingTalkAgentService()

    def open(self) -> None:
        self.tasks = TaskStore(self.database)
        self.events = EventStore(self.database)
        self.outbox = OutboxStore(self.database)
        self.agent_tasks = AgentTaskStore(self.database)
        self.tasks.recover_stale_tasks(int(os.environ.get("TASK_LOCK_TIMEOUT_SECONDS", "900")))

    def close(self) -> None:
        for store in (self.agent_tasks, self.events, self.outbox, self.tasks):
            if store is not None:
                store.close()

    def _stores(self) -> tuple[TaskStore, EventStore, OutboxStore, AgentTaskStore]:
        if not all((self.tasks, self.events, self.outbox, self.agent_tasks)):
            raise RuntimeError("Event task worker is not open")
        return self.tasks, self.events, self.outbox, self.agent_tasks  # type: ignore[return-value]

    def _publish(self, event_type: str, task_id: str, payload: dict) -> None:
        _, _, outbox, _ = self._stores()
        event = make_event(task_id, event_type, payload)
        outbox.enqueue(event)
        try:
            self.broker.publish(event_type, event)
        except Exception as error:
            outbox.mark_failed(event["event_id"], str(error))
            raise
        outbox.mark_published(event["event_id"])

    def handle(self, event: dict) -> None:
        _, events, _, _ = self._stores()
        if not events.claim(event["event_id"], event["event_type"]):
            return
        if event["event_type"] != "mail.task.created":
            raise ValueError(f"Unsupported event type: {event['event_type']}")
        self.process_task(event["task_id"])

    def process_task(self, task_id: str) -> None:
        tasks, _, _, agent_tasks = self._stores()
        item = tasks.claim(task_id, self.worker_id)
        if item is None:
            return
        self._process_claimed(item, agent_tasks)

    def _process_claimed(self, item: tuple[str, dict, int], agent_tasks: AgentTaskStore) -> None:
        tasks, _, _, _ = self._stores()
        task_id, payload, retry_count = item
        try:
            reviewer_name = str(payload.get("reviewer_name", "")).strip()
            user_id = str(payload.get("dingtalk_user_id", "")).strip()
            if not reviewer_name or not user_id:
                raise ValueError("Mail task has no DingTalk reviewer route")
            if not payload.get("projects"):
                raise ValueError("Mail task has no project and branch")
            access_token = agent_tasks.create(task_id, reviewer_name, user_id, payload)
            delivery = self.dingtalk.dispatch(
                task_id=task_id, access_token=access_token, reviewer_name=reviewer_name,
                dingtalk_user_id=user_id, context=payload,
            )
            result = {"dingtalk_delivery": delivery, "agent_task_status": "READY"}
            if not tasks.transition(task_id, "PROCESSING", "DONE", result_payload=result):
                raise RuntimeError("Task state changed before DingTalk dispatch completed")
            self._publish("dingtalk.agent.dispatched", task_id, {"dingtalk_user_id": user_id})
        except Exception as error:
            LOGGER.exception("DingTalk Agent dispatch failed task_id=%s", task_id)
            tasks.transition(task_id, "PROCESSING", "FAILED", error=str(error))
            if retry_count + 1 < MAX_RETRIES:
                tasks.transition(task_id, "FAILED", "NEW")
                self._publish("mail.task.created", task_id, {"retry": retry_count + 1})


def main() -> int:
    parser = argparse.ArgumentParser(description="Route mail tasks to a DingTalk enterprise Agent")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--consume", action="store_true")
    args = parser.parse_args()
    load_env_file(args.env_file)
    logging.basicConfig(level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
    broker = RabbitMQ()
    worker = EventTaskWorker(args.database, broker)
    try:
        if args.consume:
            broker.consume("mail.task.created", worker.handle, worker_start=worker.open, worker_stop=worker.close)
        else:
            worker.open()
            for item in worker.tasks.claim_new(worker.worker_id) if worker.tasks else []:
                worker._process_claimed(item, worker.agent_tasks)  # type: ignore[arg-type]
            worker.close()
    finally:
        broker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
