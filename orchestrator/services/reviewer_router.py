from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ReviewerRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewerRoute:
    name: str
    receive_id_type: str
    receive_id: str


class ReviewerRouter:
    """Resolve readable mail mentions to stable Feishu recipient identifiers."""

    def __init__(self, reviewers: dict[str, dict[str, object]] | None = None) -> None:
        self.reviewers = reviewers or {}

    @classmethod
    def from_file(cls, path: Path) -> "ReviewerRouter":
        if not path.exists():
            return cls()
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ReviewerRoutingError("reviewers config must be a JSON object")
        return cls(value)

    def resolve(self, name: str) -> ReviewerRoute:
        key = name.strip()
        settings = self.reviewers.get(key)
        if settings is not None:
            if not isinstance(settings, dict) or settings.get("enabled", True) is False:
                raise ReviewerRoutingError(f"Reviewer is disabled: {key}")
            receive_id = str(settings.get("receive_id") or settings.get("open_id") or "").strip()
            receive_id_type = str(settings.get("receive_id_type") or ("open_id" if settings.get("open_id") else "")).strip()
            if not receive_id or not receive_id_type:
                raise ReviewerRoutingError(f"Reviewer route is incomplete: {key}")
            return ReviewerRoute(key, receive_id_type, receive_id)
        if self.reviewers:
            raise ReviewerRoutingError(f"Reviewer is not configured: {key}")
        receive_id = os.environ.get("FEISHU_RECEIVE_ID", "").strip()
        receive_id_type = os.environ.get("FEISHU_RECEIVE_ID_TYPE", "email").strip()
        if receive_id:
            return ReviewerRoute(key, receive_id_type, receive_id)
        raise ReviewerRoutingError(f"Reviewer is not configured: {key}")
