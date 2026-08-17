from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from orchestrator.time_utils import now_beijing


class TaskStatus(str, Enum):
    NEW = "NEW"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class EventType(str, Enum):
    MAIL_TASK_CREATED = "mail.task.created"
    ANALYSIS_REQUESTED = "analysis.requested"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"
    DINGTALK_AGENT_DISPATCHED = "dingtalk.agent.dispatched"


@dataclass(frozen=True)
class TaskEvent:
    event_id: str
    task_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=now_beijing)

    def __post_init__(self) -> None:
        if not self.event_id or not self.task_id:
            raise ValueError("event_id and task_id are required")
        EventType(self.event_type)
        datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "task_id": self.task_id, "event_type": self.event_type,
                "created_at": self.created_at, "payload": self.payload}
