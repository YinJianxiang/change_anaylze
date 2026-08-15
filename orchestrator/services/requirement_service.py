from __future__ import annotations

from typing import Any


class RequirementService:
    """First version preserves URLs; document fetching is intentionally deferred."""

    def prepare(self, urls: list[str]) -> dict[str, Any]:
        documents = []
        seen: set[str] = set()
        for value in urls or []:
            url = str(value).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            documents.append({"url": url, "status": "NOT_FETCHED", "content": None})
        return {"documents": documents}
