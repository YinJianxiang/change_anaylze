from __future__ import annotations

import uuid
from typing import Any

from orchestrator.models.task import EventType, TaskEvent
from orchestrator.time_utils import now_beijing


def make_event(task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not task_id or not event_type:
        raise ValueError("task_id and event_type are required")
    EventType(event_type)
    return TaskEvent(uuid.uuid4().hex, task_id, event_type, payload,
                     now_beijing()).to_dict()


EVENT_TYPES = frozenset(item.value for item in EventType)
