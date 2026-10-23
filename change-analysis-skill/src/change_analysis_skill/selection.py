"""Deterministic selection_plan and ImpactUnit grouping for analysis handoff."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Iterable

from .guide_match import applicable_guide_sections

LAYER_NAMES = ("controller", "service", "mapper", "dao", "repository", "job", "consumer", "config", "api")
LAYER_RE = re.compile(
    r"(?i)/(?:(?:src/(?:main|test)/(?:java|kotlin|resources)/)|)"
    r"(?:.*/)?(?P<layer>controller|service|mapper|dao|repository|job|jobs|consumer|config|api)(?:/|$)"
)
STEM_SUFFIX_RE = re.compile(
    r"(?i)(?P<stem>.+?)(?P<suffix>Controller|ServiceImpl|Service|Mapper|Dao|Repository|Job|Consumer|Listener)?$"
)
DIFF_HEADER_RE = re.compile(r"(?m)^diff --git a/(.*?) b/(.*?)$")


def resolve_effort(value: str | None = None) -> str:
    raw = (value or os.environ.get("ANALYSIS_EFFORT") or "medium").strip().lower()
    if raw not in {"low", "medium", "high"}:
        return "medium"
    return raw


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def _file_stem_key(path: str) -> str:
    name = PurePosixPath(_posix(path)).stem
    match = STEM_SUFFIX_RE.match(name)
    if not match:
        return name.lower()
    stem = match.group("stem") or name
    return stem.lower()


def _layer_bucket(path: str) -> str:
    normalized = _posix(path)
    match = LAYER_RE.search("/" + normalized)
    if match:
        layer = match.group("layer").lower()
        if layer == "jobs":
            return "job"
        return layer
    lower = normalized.lower()
    for layer in LAYER_NAMES:
        if f"/{layer}/" in lower or lower.endswith(f"{layer}.java"):
            return layer
    parts = PurePosixPath(normalized).parts
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else "misc"


def _package_bucket(path: str) -> str:
    normalized = _posix(path)
    for marker in ("/src/main/java/", "src/main/java/", "/src/test/java/", "src/test/java/"):
        if marker in normalized:
            package_path = normalized.split(marker, 1)[1]
            parts = PurePosixPath(package_path).parts
            if len(parts) >= 2:
                return ".".join(parts[:-1][:4])
            break
    return _layer_bucket(path)


def _diff_sections(diff: str) -> dict[str, str]:
    matches = list(DIFF_HEADER_RE.finditer(diff))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff)
        path = _posix(match.group(2) or match.group(1) or "unknown")
        sections[path] = diff[start:end]
    return sections


def _changed_paths(snapshot: dict[str, Any]) -> list[str]:
    context = snapshot.get("change_context")
    paths: list[str] = []
    if isinstance(context, dict):
        for item in context.get("changed_files") or []:
            if isinstance(item, dict) and item.get("path"):
                paths.append(_posix(str(item["path"])))
            elif isinstance(item, str):
                paths.append(_posix(item))
    if not paths:
        for item in snapshot.get("changed_files") or []:
            paths.append(_posix(str(item)))
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            ordered.append(path)
            seen.add(path)
    return ordered


def _java_ast_files(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    impact = snapshot.get("repository_impact")
    if not isinstance(impact, dict):
        return []
    java_ast = impact.get("java_ast")
    if not isinstance(java_ast, dict):
        return []
    files = java_ast.get("files") or []
    return [item for item in files if isinstance(item, dict)]


def _scope_count_for(path: str, java_files: list[dict[str, Any]]) -> int:
    target = _posix(path)
    for item in java_files:
        if _posix(str(item.get("path") or "")) == target:
            return len(item.get("changed_scopes") or [])
    return 0


def _scopes_for(path: str, java_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = _posix(path)
    for item in java_files:
        if _posix(str(item.get("path") or "")) == target:
            scopes = item.get("changed_scopes") or []
            return [scope for scope in scopes if isinstance(scope, dict)]
    return []


def _candidate_count_for(path: str, snapshot: dict[str, Any]) -> int:
    impact = snapshot.get("repository_impact")
    if not isinstance(impact, dict):
        return 0
    total = 0
    target = _posix(path)
    for key in ("symbol_references", "api_references", "backend_callers", "related_tests"):
        for item in impact.get(key) or []:
            if isinstance(item, dict):
                ref_path = _posix(str(item.get("path") or item.get("file") or ""))
                if ref_path == target or target in ref_path:
                    total += 1
            elif isinstance(item, str) and target in _posix(item):
                total += 1
    return total


def _related_candidates(paths: Iterable[str], snapshot: dict[str, Any], limit: int = 20) -> list[Any]:
    impact = snapshot.get("repository_impact")
    if not isinstance(impact, dict):
        return []
    path_set = {_posix(path) for path in paths}
    stems = {_file_stem_key(path) for path in path_set}
    selected: list[Any] = []
    for key in ("symbol_references", "api_references", "backend_callers", "related_tests", "caller_candidates"):
        values = impact.get(key)
        if key == "caller_candidates" and not values:
            slice_value = impact.get("impact_slice")
            if isinstance(slice_value, dict):
                values = slice_value.get("caller_candidates")
        for item in values or []:
            text = ""
            item_path = ""
            if isinstance(item, dict):
                item_path = _posix(str(item.get("path") or item.get("file") or ""))
                text = str(item.get("symbol") or item.get("qualified_name") or item.get("name") or item_path)
            else:
                text = str(item)
            if item_path in path_set or any(stem and stem in text.lower() for stem in stems):
                selected.append(item)
            if len(selected) >= limit:
                return selected
    return selected


def build_selection_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Describe which files enter analysis and why some evidence is light/truncated."""
    java_files = _java_ast_files(snapshot)
    java_paths = {_posix(str(item.get("path") or "")) for item in java_files}
    impact = snapshot.get("repository_impact") if isinstance(snapshot.get("repository_impact"), dict) else {}
    warnings = [str(item) for item in (snapshot.get("evidence_warnings") or [])]
    limits = [str(item) for item in (impact.get("analysis_limits") or [])]
    truncated_refs = bool(impact.get("search_truncated")) or any("_truncated_items" in str(item) for item in limits)
    files: list[dict[str, Any]] = []

    for path in _changed_paths(snapshot):
        reasons: list[str] = []
        status = "included"
        is_java = path.endswith(".java") or path.endswith(".kt")
        scope_count = _scope_count_for(path, java_files)
        candidate_count = _candidate_count_for(path, snapshot)

        if snapshot.get("change_context") is None and snapshot.get("repository_impact") is None:
            status = "evidence_failed"
            reasons.append("change_context and repository_impact are missing")
        elif is_java and path not in java_paths and snapshot.get("repository_impact") is not None:
            status = "summary_only"
            reasons.append("Java file had no intersecting AST changed scopes")
        elif not is_java:
            status = "non_java_light"
            reasons.append("Non-Java files keep changed-line evidence only")

        if snapshot.get("diff_truncated_for_analysis"):
            if status == "included":
                status = "truncated"
            reasons.append("Summary diff was truncated for analysis_input")

        if truncated_refs and candidate_count:
            reasons.append("Directed reference search hit configured limits")

        for warning in warnings:
            if path in warning or "repository_impact" in warning or "change_context" in warning:
                if "failed" in warning.lower() or ":" in warning:
                    if status == "included":
                        status = "evidence_failed"
                    reasons.append(warning)

        files.append({
            "path": path,
            "status": status,
            "reasons": reasons or ["Included in changed-scope analysis"],
            "ast_scope_count": scope_count,
            "candidate_reference_count": candidate_count,
        })

    return {
        "files": files,
        "summary": {
            "changed_files": len(files),
            "included": sum(1 for item in files if item["status"] == "included"),
            "non_java_light": sum(1 for item in files if item["status"] == "non_java_light"),
            "truncated": sum(1 for item in files if item["status"] == "truncated"),
            "summary_only": sum(1 for item in files if item["status"] == "summary_only"),
            "evidence_failed": sum(1 for item in files if item["status"] == "evidence_failed"),
        },
        "analysis_limits": limits,
        "evidence_warnings": warnings,
    }


def _unit_label(bucket: str, paths: list[str]) -> str:
    stems = sorted({_file_stem_key(path) for path in paths})
    if len(stems) == 1:
        return f"{bucket}:{stems[0]}"
    if len(paths) <= 3:
        return f"{bucket}:{','.join(PurePosixPath(path).name for path in paths)}"
    return f"{bucket}:{len(paths)}-files"


def build_impact_units(
    snapshot: dict[str, Any],
    *,
    max_files_per_unit: int = 10,
    repository_index: int = 0,
) -> list[dict[str, Any]]:
    """Group related changed files into ImpactUnits without calling a model."""
    paths = _changed_paths(snapshot)
    if not paths:
        return []
    java_files = _java_ast_files(snapshot)
    sections = _diff_sections(str(snapshot.get("diff") or ""))
    max_files = max(int(max_files_per_unit), 1)

    # First cluster by business stem within a package/layer bucket.
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in paths:
        bucket = _package_bucket(path)
        stem = _file_stem_key(path)
        groups[(bucket, stem)].append(path)

    # Merge tiny same-bucket groups so Controller/Service/Mapper of different stems
    # in the same package can still share a unit when under the file cap.
    by_bucket: dict[str, list[list[str]]] = defaultdict(list)
    for (bucket, _stem), group_paths in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        by_bucket[bucket].append(group_paths)

    units: list[dict[str, Any]] = []
    unit_counter = 0
    for bucket, clusters in by_bucket.items():
        current: list[str] = []
        for cluster in clusters:
            if len(cluster) > max_files:
                if current:
                    unit_counter += 1
                    units.append(_make_unit(unit_counter, bucket, current, snapshot, java_files, sections, repository_index))
                    current = []
                for offset in range(0, len(cluster), max_files):
                    unit_counter += 1
                    units.append(
                        _make_unit(
                            unit_counter,
                            bucket,
                            cluster[offset:offset + max_files],
                            snapshot,
                            java_files,
                            sections,
                            repository_index,
                        )
                    )
                continue
            if current and len(current) + len(cluster) > max_files:
                unit_counter += 1
                units.append(_make_unit(unit_counter, bucket, current, snapshot, java_files, sections, repository_index))
                current = []
            current.extend(cluster)
        if current:
            unit_counter += 1
            units.append(_make_unit(unit_counter, bucket, current, snapshot, java_files, sections, repository_index))
    return units


def _make_unit(
    index: int,
    bucket: str,
    paths: list[str],
    snapshot: dict[str, Any],
    java_files: list[dict[str, Any]],
    sections: dict[str, str],
    repository_index: int,
) -> dict[str, Any]:
    scopes: list[dict[str, Any]] = []
    for path in paths:
        scopes.extend(_scopes_for(path, java_files))
    diff_parts = [sections[path] for path in paths if path in sections]
    diff_text = "".join(diff_parts)
    return {
        "id": f"unit-{repository_index}-{index}",
        "label": _unit_label(bucket, paths),
        "repository_index": repository_index,
        "bucket": bucket,
        "files": paths,
        "changed_scopes": scopes,
        "related_candidates": _related_candidates(paths, snapshot),
        "applicable_guide_sections": applicable_guide_sections(paths),
        "diff_byte_estimate": len(diff_text.encode("utf-8")),
    }


def build_analysis_planning(
    snapshots: list[dict[str, Any]],
    *,
    effort: str | None = None,
    max_files_per_unit: int = 10,
) -> dict[str, Any]:
    """Build selection_plan and impact_units for one or more repository snapshots."""
    resolved_effort = resolve_effort(effort)
    selection_plans = []
    impact_units: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots):
        plan = build_selection_plan(snapshot)
        plan["repository_index"] = index
        plan["project"] = snapshot.get("project") or snapshot.get("repository_name") or "repository"
        selection_plans.append(plan)
        impact_units.extend(
            build_impact_units(snapshot, max_files_per_unit=max_files_per_unit, repository_index=index)
        )
    return {
        "effort": resolved_effort,
        "selection_plan": selection_plans[0] if len(selection_plans) == 1 else {
            "repositories": selection_plans,
            "summary": {
                "repositories": len(selection_plans),
                "changed_files": sum(int(item["summary"]["changed_files"]) for item in selection_plans),
                "impact_units": len(impact_units),
            },
        },
        "impact_units": impact_units,
    }


def format_preview(planning: dict[str, Any]) -> str:
    """Human-readable preview for CLI --preview."""
    lines = [
        f"effort: {planning.get('effort', 'medium')}",
        f"impact_units: {len(planning.get('impact_units') or [])}",
        "",
        "selection_plan:",
    ]
    selection = planning.get("selection_plan") or {}
    plans = selection.get("repositories") if isinstance(selection, dict) and "repositories" in selection else [selection]
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        project = plan.get("project") or "repository"
        summary = plan.get("summary") or {}
        lines.append(f"- {project}: {summary}")
        for item in plan.get("files") or []:
            reasons = "; ".join(item.get("reasons") or [])
            lines.append(
                f"  - [{item.get('status')}] {item.get('path')} "
                f"(scopes={item.get('ast_scope_count', 0)}, candidates={item.get('candidate_reference_count', 0)}) "
                f"{reasons}"
            )
    lines.append("")
    lines.append("impact_units:")
    for unit in planning.get("impact_units") or []:
        lines.append(
            f"- {unit.get('id')} {unit.get('label')} files={len(unit.get('files') or [])} "
            f"bytes~{unit.get('diff_byte_estimate', 0)} guides={','.join(unit.get('applicable_guide_sections') or [])}"
        )
        for path in unit.get("files") or []:
            lines.append(f"  - {path}")
    return "\n".join(lines) + "\n"
