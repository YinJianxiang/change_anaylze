"""Canonical task worker entry point; legacy task_runner remains compatible."""
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
from orchestrator.services.feishu_service import FeishuService
from orchestrator.services.analysis_context import enrich_snapshots, llm_snapshots
from orchestrator.services.git_service import GitService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.requirement_service import RequirementService
from orchestrator.storage.event_store import EventStore
from orchestrator.storage.outbox_store import OutboxStore
from orchestrator.storage.task_store import TaskStore
from orchestrator.task_runner import PermanentTaskError, load_projects_config, run_task, retry_task

MAX_RETRIES = 3
LOGGER = logging.getLogger(__name__)


class EventTaskWorker:
    def __init__(self, database: Path, config: Path, broker: RabbitMQ) -> None:
        self.database = database
        self.tasks: TaskStore | None = None
        self.events: EventStore | None = None
        self.outbox: OutboxStore | None = None
        self.config = load_projects_config(config)
        self.broker = broker
        self.worker_id = f"task-worker-{uuid.uuid4().hex}"
        self.git = GitService()
        self.requirements = RequirementService()
        self.llm = LLMService()
        self.feishu = FeishuService()

    def open(self) -> None:
        self.tasks = TaskStore(self.database)
        self.events = EventStore(self.database)
        self.outbox = OutboxStore(self.database)
        recovered = self.tasks.recover_stale_tasks(int(os.environ.get("TASK_LOCK_TIMEOUT_SECONDS", "900")))
        LOGGER.info("worker opened worker_id=%s database=%s recovered_tasks=%d", self.worker_id, self.database, recovered)

    def close(self) -> None:
        if self.events is not None:
            self.events.close()
        if self.outbox is not None:
            self.outbox.close()
        if self.tasks is not None:
            self.tasks.close()

    def _stores(self) -> tuple[TaskStore, EventStore, OutboxStore]:
        if self.tasks is None or self.events is None or self.outbox is None:
            raise RuntimeError("Event task worker is not open")
        return self.tasks, self.events, self.outbox

    def _publish(self, event_type: str, task_id: str, payload: dict) -> None:
        _, _, outbox = self._stores()
        event = make_event(task_id, event_type, payload)
        LOGGER.info("publishing event task_id=%s event_type=%s", task_id, event_type)
        outbox.enqueue(event)
        try:
            self.broker.publish(event_type, event)
        except Exception as error:
            outbox.mark_failed(event["event_id"], str(error))
            raise
        outbox.mark_published(event["event_id"])
        LOGGER.info("event published task_id=%s event_type=%s event_id=%s", task_id, event_type, event["event_id"])

    def handle(self, event: dict) -> None:
        tasks, events, _ = self._stores()
        event_id, event_type, task_id = event["event_id"], event["event_type"], event["task_id"]
        LOGGER.info("event received task_id=%s event_type=%s event_id=%s", task_id, event_type, event_id)
        if not events.claim(event_id, event_type):
            LOGGER.info("duplicate event skipped event_id=%s", event_id)
            return
        if event_type == "mail.task.created":
            self.process_task(task_id)
        elif event_type == "feishu.confirmation.received":
            action = event.get("payload", {}).get("action")
            target = "DONE" if action == "confirm_success" else "REJECTED" if action == "reject" else None
            if target is None:
                raise ValueError("Unsupported confirmation action")
            tasks.transition(task_id, "WAITING_CONFIRM", target,
                             feedback=event.get("payload", {}).get("feedback", ""))
            LOGGER.info("confirmation applied task_id=%s status=%s", task_id, target)

    def process_task(self, task_id: str) -> None:
        tasks, _, _ = self._stores()
        item = tasks.claim(task_id, self.worker_id)
        if item is None:
            LOGGER.info("task not claimable task_id=%s", task_id)
            return
        _, payload, retry_count = item
        LOGGER.info("task claimed task_id=%s retry_count=%d", task_id, retry_count)
        try:
            if payload.get("routing_error"):
                raise PermanentTaskError(str(payload["routing_error"]))
            projects = payload.get("projects", [])
            if not projects:
                raise PermanentTaskError("No project branches found in the mail payload")
            self._publish("analysis.requested", task_id, {})
            repositories = []
            for value in projects:
                LOGGER.info("git snapshot started task_id=%s project=%s branch=%s", task_id, value["project"], value["branch"])
                repositories.append(self.git.snapshot(value["project"], value["branch"], self.config))
                LOGGER.info("git snapshot completed task_id=%s project=%s", task_id, value["project"])
            repositories, analysis_mode, evidence_warnings = enrich_snapshots(repositories)
            documents = self.requirements.prepare(payload.get("requirement_urls", []))["documents"]
            LOGGER.info("requirements prepared task_id=%s documents=%d", task_id, len(documents))
            LOGGER.info("llm analysis started task_id=%s repositories=%d", task_id, len(repositories))
            analysis = self.llm.analyze({
                "requirements": documents,
                "requirement_urls": payload.get("requirement_urls", []),
                "repositories": llm_snapshots(repositories),
                "analysis_mode": analysis_mode,
                "evidence_warnings": evidence_warnings,
            })
            LOGGER.info("llm analysis completed task_id=%s", task_id)
            self._publish("analysis.completed", task_id, {"analysis": analysis})
            LOGGER.info("feishu send started task_id=%s reviewer=%s", task_id, payload.get("reviewer_name", ""))
            robot_message_id = self.feishu.send_analysis(
                task_id, analysis, receive_id=str(payload.get("receiver_id", "")),
                receive_id_type=str(payload.get("receiver_id_type", "")))
            LOGGER.info("feishu send completed task_id=%s robot_message_id=%s", task_id, robot_message_id)
            result = {"repositories": repositories, "requirement_documents": documents, "analysis": analysis}
            if not tasks.transition(task_id, "PROCESSING", "WAITING_CONFIRM", result_payload=result,
                                    robot_message_id=robot_message_id):
                raise RuntimeError("Task state changed before completion")
            self._publish("feishu.analysis.ready", task_id, {"robot_message_id": robot_message_id})
            LOGGER.info("task waiting for confirmation task_id=%s", task_id)
        except Exception as error:
            LOGGER.exception("task failed task_id=%s retry_count=%d", task_id, retry_count)
            tasks.transition(task_id, "PROCESSING", "FAILED", error=str(error))
            if not isinstance(error, PermanentTaskError) and retry_count + 1 < MAX_RETRIES:
                tasks.transition(task_id, "FAILED", "NEW")
                self._publish("mail.task.created", task_id, {"retry": retry_count + 1})
            self._publish("analysis.failed", task_id, {"error": str(error)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run queued mail analysis tasks.")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--config", type=Path, default=Path(".local/projects.json"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retry-message-id")
    parser.add_argument("--consume", action="store_true", help="Consume RabbitMQ task and confirmation events")
    args = parser.parse_args()
    load_env_file(args.env_file)
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.consume:
        LOGGER.info("connecting to RabbitMQ")
        broker = RabbitMQ()
        LOGGER.info("RabbitMQ connected; waiting for task and confirmation events")
        worker = EventTaskWorker(args.database, args.config, broker)
        try:
            broker.consume_many(["mail.task.created", "feishu.confirmation.received"], worker.handle,
                                worker_start=worker.open, worker_stop=worker.close)
        finally:
            broker.close()
    elif args.retry_message_id:
        print(json.dumps({"retried": retry_task(args.database, args.retry_message_id)}))
    else:
        print(json.dumps(run_task(args.database, args.config, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
