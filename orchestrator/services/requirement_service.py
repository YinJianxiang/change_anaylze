"""Requirement document preparation for analysis tasks."""

from __future__ import annotations

import os
from typing import Any

from orchestrator.services.requirement_fetchers.pipeline import fetch_requirement_document


def _fetch_enabled() -> bool:
    value = os.environ.get("REQUIREMENT_FETCH_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


class RequirementService:
    """Normalize requirement URLs and optionally fetch document bodies."""

    def prepare(self, urls: list[str]) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        enabled = _fetch_enabled()
        for value in urls or []:
            url = str(value).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            if not enabled:
                documents.append({"url": url, "status": "NOT_FETCHED", "content": None, "meta": {"fetch_enabled": False}})
                continue
            documents.append(fetch_requirement_document(url))
        return {"documents": documents}
