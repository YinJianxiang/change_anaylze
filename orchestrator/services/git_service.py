"""Git analysis service boundary."""
from __future__ import annotations

from typing import Any


class GitService:
    """Fetch repositories and produce a pinned change snapshot."""

    def snapshot(self, project: str, branch: str, config: dict[str, dict[str, str]]) -> dict[str, Any]:
        # Imported lazily to keep the compatibility CLI stable during the
        # incremental extraction from task_runner.
        from orchestrator.task_runner import git_snapshot

        return git_snapshot(project, branch, config)
