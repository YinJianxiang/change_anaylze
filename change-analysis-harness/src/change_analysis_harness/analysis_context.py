"""Collect the repository evidence required by the change-analysis skill."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


EVIDENCE_ROOT = Path(__file__).resolve().parent / "evidence"
CHANGE_SCRIPT = EVIDENCE_ROOT / "collect_change_context.py"
IMPACT_SCRIPT = EVIDENCE_ROOT / "collect_repository_impact.py"


class EvidenceCollectionError(RuntimeError):
    pass


def _run_script(script: Path, args: list[str], timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONIOENCODING": "utf-8",
        },
        check=False,
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
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[diff truncated before direct analysis]\n", True


def enrich_snapshot(snapshot: dict[str, Any], timeout: int = 60, max_diff_bytes: int = 0) -> dict[str, Any]:
    """Attach skill evidence; preserve the snapshot when evidence collection fails."""
    enriched = dict(snapshot)
    if max_diff_bytes > 0:
        diff, truncated = _truncate_utf8(str(snapshot.get("diff", "")), max_diff_bytes)
        enriched["diff"] = diff
        enriched["diff_truncated_for_analysis"] = truncated
    else:
        enriched["diff_truncated_for_analysis"] = False
    repo_value = snapshot.get("local_path")
    revision_base = snapshot.get("merge_base")
    revision_head = snapshot.get("target_commit")
    if not repo_value or not revision_base or not revision_head:
        enriched["analysis_mode"] = "Diff-only evidence analysis"
        enriched["change_context"] = None
        enriched["repository_impact"] = None
        enriched["evidence_warnings"] = ["Repository snapshot lacks local_path or revision metadata"]
        return enriched
    repo = Path(str(repo_value))
    revision_range = f"{revision_base}...{revision_head}"
    enriched["evidence_warnings"] = []
    stages = (
        ("change_context", CHANGE_SCRIPT,
         ["--repo", str(repo), "--range", revision_range, "--analysis-mode", "changed-scope"]),
        ("repository_impact", IMPACT_SCRIPT, ["--repo", str(repo), "--range", revision_range]),
    )
    for key, script, args in stages:
        try:
            enriched[key] = _run_script(script, args, timeout)
        except (EvidenceCollectionError, OSError, subprocess.TimeoutExpired) as exc:
            enriched[key] = None
            enriched["evidence_warnings"].append(f"{key}: {exc}")
    if not enriched["evidence_warnings"]:
        enriched["analysis_mode"] = "Changed-scope AST evidence analysis"
    elif any(enriched[key] is not None for key, _, _ in stages):
        enriched["analysis_mode"] = "Partial evidence analysis"
    else:
        enriched["analysis_mode"] = "Diff-only evidence analysis"
    return enriched


def enrich_snapshots(snapshots: list[dict[str, Any]], timeout: int = 60, max_diff_bytes: int = 0) -> tuple[list[dict[str, Any]], str, list[str]]:
    enriched = [enrich_snapshot(item, timeout, max_diff_bytes) for item in snapshots]
    warnings = [warning for item in enriched for warning in item.get("evidence_warnings", [])]
    warnings.extend("Diff was truncated before Codex analysis for " + str(item.get("project", "repository"))
                    for item in enriched if item.get("diff_truncated_for_analysis"))
    if not warnings:
        mode = "Changed-scope AST evidence analysis"
    elif any(item.get(key) is not None for item in enriched for key in ("change_context", "repository_impact")):
        mode = "Partial evidence analysis"
    else:
        mode = "Diff-only evidence analysis"
    return enriched, mode, warnings


def _cap_evidence_list(value: object, limit: int = 3) -> object:
    if not isinstance(value, list) or len(value) <= limit:
        return value
    return [*value[:limit], {"_truncated_items": len(value) - limit}]


def codex_snapshots(snapshots: list[dict[str, Any]], max_diff_bytes: int = 0) -> list[dict[str, Any]]:
    """Build a bounded analysis view without modifying the collected evidence.

    Repository impact collection is intentionally limited to changed files and
    changed AST scopes. Keep the focused impact slice and bounded reference
    lists in the handoff; no whole-repository call graph is produced.
    """
    result = []
    for snapshot in snapshots:
        item = dict(snapshot)
        if max_diff_bytes > 0:
            item["diff"], item["diff_truncated_for_analysis"] = _truncate_utf8(
                str(snapshot.get("diff", "")), max_diff_bytes
            )
        context = item.get("change_context")
        if isinstance(context, dict) and "unified_diff" in context:
            context = dict(context)
            context.pop("unified_diff", None)
            item["change_context"] = context

        impact = item.get("repository_impact")
        if isinstance(impact, dict):
            impact = dict(impact)
            for key in (
                "changed_symbols",
                "changed_callees",
                "symbol_references",
                "api_references",
                "frontend_consumers",
                "backend_callers",
                "database_dependencies",
                "configuration_references",
                "scheduled_jobs",
                "message_consumers",
                "related_tests",
                "unresolved_symbols",
            ):
                if key in impact:
                    impact[key] = _cap_evidence_list(impact[key])
            slice_value = impact.get("impact_slice")
            if isinstance(slice_value, dict):
                slice_value = dict(slice_value)
                for key in ("change_seeds", "confirmed_paths", "probable_paths", "candidate_edges"):
                    if key in slice_value:
                        slice_value[key] = _cap_evidence_list(slice_value[key])
                impact["impact_slice"] = slice_value
            item["repository_impact"] = impact
        result.append(item)
    return result

