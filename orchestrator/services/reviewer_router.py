from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ReviewerRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewerRoute:
    name: str
    dingtalk_user_id: str


class ReviewerRouter:
    """Resolve readable mail mentions to stable DingTalk user IDs."""

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
            user_id = str(settings.get("dingtalk_user_id") or "").strip()
            if not user_id:
                raise ReviewerRoutingError(f"Reviewer route is incomplete: {key}")
            return ReviewerRoute(key, user_id)
        raise ReviewerRoutingError(f"Reviewer is not configured: {key}")
