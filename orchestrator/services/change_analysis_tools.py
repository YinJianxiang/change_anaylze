"""Read-only repository tools exposed to enterprise agents through MCP."""
from __future__ import annotations

import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from orchestrator.services.analysis_context import enrich_snapshot
from orchestrator.services.git_service import GitService
from orchestrator.storage.agent_task_store import AgentTaskStore


class AnalysisToolError(ValueError):
    pass


@dataclass
class AnalysisSession:
    snapshot: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    evidence: dict[str, Any] | None = None


class ChangeAnalysisTools:
    """Bounded, read-only operations over configured repositories."""

    def __init__(
        self,
        projects: dict[str, dict[str, str]],
        *,
        ttl_seconds: int = 3600,
        evidence_timeout: int = 120,
        task_database: Path | None = None,
    ) -> None:
        self.projects = projects
        self.ttl_seconds = ttl_seconds
        self.evidence_timeout = evidence_timeout
        self.task_database = task_database
        self._sessions: dict[str, AnalysisSession] = {}
        self._lock = threading.Lock()

    def get_mail_analysis_task(self, task_id: str, agent_access_token: str) -> dict[str, Any]:
        if self.task_database is None:
            raise AnalysisToolError("Mail task access is not configured")
        store = AgentTaskStore(self.task_database)
        try:
            context = store.get_context(task_id, agent_access_token)
        finally:
            store.close()
        if context is None:
            raise AnalysisToolError("Unknown task or invalid agent access token")
        return context

    def complete_mail_analysis_task(
        self, task_id: str, agent_access_token: str, report: dict[str, Any]
    ) -> dict[str, bool]:
        if self.task_database is None:
            raise AnalysisToolError("Mail task access is not configured")
        store = AgentTaskStore(self.task_database)
        try:
            completed = store.complete(task_id, agent_access_token, report)
        finally:
            store.close()
        return {"completed": completed}

    def prepare_change_workspace(
        self, project: str, target_branch: str, base_branch: str | None = None
    ) -> dict[str, Any]:
        settings = self.projects.get(project)
        if not settings:
            raise AnalysisToolError(f"Unknown or unauthorized project: {project}")
        if not target_branch.strip():
            raise AnalysisToolError("target_branch is required")
        effective = {key: dict(value) for key, value in self.projects.items()}
        if base_branch:
            effective[project]["base_branch"] = base_branch
        snapshot = GitService().snapshot(project, target_branch, effective)
        analysis_id = "analysis_" + secrets.token_urlsafe(18)
        with self._lock:
            self._discard_expired_locked()
            self._sessions[analysis_id] = AnalysisSession(snapshot=snapshot)
        return {
            "analysis_id": analysis_id,
            "project": project,
            "base_branch": snapshot.get("base_branch"),
            "target_branch": snapshot.get("target_branch"),
            "base_commit": snapshot.get("base_commit"),
            "merge_base": snapshot.get("merge_base"),
            "target_commit": snapshot.get("target_commit"),
            "changed_files": snapshot.get("changed_files", []),
        }

    def collect_change_context(self, analysis_id: str) -> dict[str, Any]:
        evidence = self._evidence(analysis_id)
        return {
            "analysis_mode": evidence.get("analysis_mode"),
            "warnings": evidence.get("evidence_warnings", []),
            "change_context": evidence.get("change_context"),
        }

    def collect_repository_impact(self, analysis_id: str) -> dict[str, Any]:
        evidence = self._evidence(analysis_id)
        return {
            "analysis_mode": evidence.get("analysis_mode"),
            "warnings": evidence.get("evidence_warnings", []),
            "repository_impact": evidence.get("repository_impact"),
        }

    def read_source_evidence(
        self, analysis_id: str, path: str, start_line: int = 1, end_line: int = 200
    ) -> dict[str, Any]:
        session = self._session(analysis_id)
        safe_path = self._safe_repo_path(path)
        if start_line < 1 or end_line < start_line or end_line - start_line > 399:
            raise AnalysisToolError("line range must contain between 1 and 400 lines")
        content = self._git(session, "show", f"{session.snapshot['target_commit']}:{safe_path}")
        lines = content.splitlines()
        selected = lines[start_line - 1:end_line]
        return {
            "path": safe_path,
            "target_commit": session.snapshot["target_commit"],
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "content": "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start_line)),
        }

    def search_repository(
        self, analysis_id: str, query: str, max_results: int = 50
    ) -> dict[str, Any]:
        session = self._session(analysis_id)
        if not query.strip() or len(query) > 200:
            raise AnalysisToolError("query must contain between 1 and 200 characters")
        if max_results < 1 or max_results > 100:
            raise AnalysisToolError("max_results must be between 1 and 100")
        output = self._git(
            session, "grep", "-n", "-I", "-F", "-e", query,
            session.snapshot["target_commit"], "--",
            allow_no_matches=True,
        )
        matches = output.splitlines()
        return {"query": query, "matches": matches[:max_results], "truncated": len(matches) > max_results}

    def release_change_workspace(self, analysis_id: str) -> dict[str, bool]:
        with self._lock:
            removed = self._sessions.pop(analysis_id, None) is not None
        return {"released": removed}

    def _evidence(self, analysis_id: str) -> dict[str, Any]:
        session = self._session(analysis_id)
        if session.evidence is None:
            session.evidence = enrich_snapshot(session.snapshot, timeout=self.evidence_timeout)
        return session.evidence

    def _session(self, analysis_id: str) -> AnalysisSession:
        with self._lock:
            self._discard_expired_locked()
            session = self._sessions.get(analysis_id)
        if session is None:
            raise AnalysisToolError("Unknown or expired analysis_id")
        return session

    def _discard_expired_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [key for key, value in self._sessions.items() if value.created_at < cutoff]
        for key in expired:
            self._sessions.pop(key, None)

    @staticmethod
    def _safe_repo_path(value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or not path.parts or ".." in path.parts or ".git" in path.parts:
            raise AnalysisToolError("path must be a repository-relative source path")
        return path.as_posix()

    @staticmethod
    def _git(
        session: AnalysisSession, *args: str, allow_no_matches: bool = False
    ) -> str:
        repo = Path(str(session.snapshot["local_path"]))
        completed = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        if completed.returncode and not (allow_no_matches and completed.returncode == 1):
            raise AnalysisToolError((completed.stderr or "git command failed").strip())
        return completed.stdout or ""
