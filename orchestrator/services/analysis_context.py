"""Collect the repository evidence required by the change-analysis skill."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[2] / "analyze-change-test-scope" / "skills" / "analyze-change-test-scope"
CHANGE_SCRIPT = SKILL_ROOT / "scripts" / "collect_change_context.py"
IMPACT_SCRIPT = SKILL_ROOT / "scripts" / "collect_repository_impact.py"


class EvidenceCollectionError(RuntimeError):
    pass


def _run_script(script: Path, args: list[str], timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "script failed").strip()
        raise EvidenceCollectionError(f"{script.name}: {detail}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EvidenceCollectionError(f"{script.name} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceCollectionError(f"{script.name} returned a non-object")
    return value


def _truncate_utf8(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[diff truncated before LLM analysis]\n", True


def enrich_snapshot(snapshot: dict[str, Any], timeout: int = 60, max_diff_bytes: int = 120_000) -> dict[str, Any]:
    """Attach skill evidence; preserve the snapshot when evidence collection fails."""
    enriched = dict(snapshot)
    diff, truncated = _truncate_utf8(str(snapshot.get("diff", "")), max_diff_bytes)
    enriched["diff"] = diff
    enriched["diff_truncated_for_llm"] = truncated
    repo_value = snapshot.get("local_path")
    revision_base = snapshot.get("merge_base")
    revision_head = snapshot.get("target_commit")
    if not repo_value or not revision_base or not revision_head:
        enriched["analysis_mode"] = "Diff-only analysis"
        enriched["change_context"] = None
        enriched["repository_impact"] = None
        enriched["evidence_warnings"] = ["Repository snapshot lacks local_path or revision metadata"]
        return enriched
    repo = Path(str(repo_value))
    revision_range = f"{revision_base}...{revision_head}"
    try:
        enriched["change_context"] = _run_script(
            CHANGE_SCRIPT, ["--repo", str(repo), "--range", revision_range, "--analysis-mode", "full"], timeout
        )
        enriched["repository_impact"] = _run_script(
            IMPACT_SCRIPT, ["--repo", str(repo), "--range", revision_range], timeout
        )
        enriched["analysis_mode"] = "Full repository context analysis"
        enriched["evidence_warnings"] = []
    except (EvidenceCollectionError, OSError, subprocess.TimeoutExpired) as exc:
        enriched["analysis_mode"] = "Diff-only analysis"
        enriched["change_context"] = None
        enriched["repository_impact"] = None
        enriched["evidence_warnings"] = [str(exc)]
    return enriched


def enrich_snapshots(snapshots: list[dict[str, Any]], timeout: int = 60, max_diff_bytes: int = 120_000) -> tuple[list[dict[str, Any]], str, list[str]]:
    enriched = [enrich_snapshot(item, timeout, max_diff_bytes) for item in snapshots]
    warnings = [warning for item in enriched for warning in item.get("evidence_warnings", [])]
    warnings.extend("Diff was truncated before LLM analysis for " + str(item.get("project", "repository"))
                    for item in enriched if item.get("diff_truncated_for_llm"))
    mode = "Full repository context analysis" if not warnings else "Diff-only analysis"
    return enriched, mode, warnings


def llm_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicated unified diff while retaining the archived evidence."""
    result = []
    for snapshot in snapshots:
        item = dict(snapshot)
        context = item.get("change_context")
        if isinstance(context, dict) and "unified_diff" in context:
            context = dict(context)
            context.pop("unified_diff", None)
            item["change_context"] = context
        result.append(item)
    return result
