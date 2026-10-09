"""Render Harness evidence as a portable Markdown report."""
from __future__ import annotations

from datetime import date
import re
from pathlib import Path
from typing import Any, Iterable


def _value(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _cell(value: Any) -> str:
    return _value(value).replace("|", r"\|").replace("\n", "<br>")


def _items(values: Iterable[Any], empty: str = "-", limit: int | None = None) -> str:
    rows = list(values)
    omitted = 0
    if limit is not None and len(rows) > limit:
        omitted = len(rows) - limit
        rows = rows[:limit]
    if not rows:
        return empty
    rendered = []
    for value in rows:
        if isinstance(value, dict):
            if "_truncated_items" in value:
                rendered.append(f"- ... {value.get('_truncated_items')} item(s) truncated by Harness.")
                continue
            text = value.get("path") or value.get("symbol") or value.get("qualified_name") or value.get("name") or str(value)
        else:
            text = str(value)
        rendered.append(f"- `{text}`")
    if omitted:
        rendered.append(f"- ... {omitted} additional item(s) omitted due to report size limit.")
    return "\n".join(rendered)


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        values = list(row)
        values.extend(["-"] * (len(headers) - len(values)))
        lines.append("| " + " | ".join(_cell(value) for value in values[: len(headers)]) + " |")
    return "\n".join(lines)


def _requirement_summary(documents: list[dict[str, Any]]) -> tuple[str, str]:
    titles: list[str] = []
    contents: list[str] = []
    for document in documents:
        meta = document.get("meta") or {}
        title = meta.get("title") or document.get("url") or "Untitled requirement"
        titles.append(str(title))
        content = str(document.get("content") or "").strip()
        if content:
            contents.append(content)
    return ", ".join(titles) if titles else "No requirement document fetched", "\n\n".join(contents)




_INVALID_DIRECTORY_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def requirement_directory_name(result: dict[str, Any]) -> str:
    """Return a safe, stable directory name for one requirement."""
    documents = result.get("requirement_documents") or []
    title = ""
    story_id = ""
    if documents:
        first = documents[0] or {}
        meta = first.get("meta") or {}
        title = str(meta.get("title") or "")
        story_id = str(meta.get("story_id") or "")
    if not title:
        title = str((result.get("request") or {}).get("requirement_url") or "requirement")
    name = _INVALID_DIRECTORY_CHARS.sub("_", title)
    name = re.sub(r"\s+", " ", name).strip(" ._") or "requirement"
    if story_id:
        safe_story_id = _INVALID_DIRECTORY_CHARS.sub("_", story_id).strip(" ._")
        if safe_story_id and safe_story_id not in name:
            name = f"{name}_{safe_story_id}"
    return name[:150].rstrip(" ._") or "requirement"


def default_report_path(result: dict[str, Any], reporter_dir: Path) -> Path:
    """Build reporter/<requirement>/change-analysis-report.md."""
    return reporter_dir / requirement_directory_name(result) / "change-analysis-report.md"


def render_markdown(result: dict[str, Any], *, generated_on: str | None = None) -> str:
    """Render a Harness JSON result as UTF-8 Markdown evidence."""
    request = result.get("request") or {}
    repository = result.get("repository") or {}
    impact = repository.get("repository_impact") or {}
    change_context = repository.get("change_context") or {}
    documents = result.get("requirement_documents") or []
    title, requirement_content = _requirement_summary(documents)
    generated_on = generated_on or date.today().isoformat()

    changed_file_rows = []
    for item in change_context.get("changed_files") or []:
        changed_file_rows.append((item.get("path"), item.get("change_type") or item.get("status"), item.get("added_lines", 0), item.get("deleted_lines", 0)))
    if not changed_file_rows:
        changed_file_rows = [(path, "changed", "-", "-") for path in repository.get("changed_files") or []]

    changed_symbols = impact.get("changed_symbols") or []
    java_files = (impact.get("java_ast") or {}).get("files") or []
    graph = (impact.get("impact_slice") or {}).get("graph_quality") or {}
    warnings = result.get("evidence_warnings") or []
    limits = impact.get("analysis_limits") or []
    candidate_refs = impact.get("symbol_references") or []
    related_tests = impact.get("related_tests") or []
    selection = result.get("selection_plan") or (result.get("analysis_input") or {}).get("selection_plan") or {}
    impact_units = result.get("impact_units") or (result.get("analysis_input") or {}).get("impact_units") or []
    selection_files = selection.get("files") if isinstance(selection, dict) else []
    if not selection_files and isinstance(selection, dict) and isinstance(selection.get("repositories"), list):
        selection_files = [
            item
            for plan in selection.get("repositories") or []
            if isinstance(plan, dict)
            for item in (plan.get("files") or [])
        ]

    lines = [
        "# Code Change Analysis Report",
        "",
        "> Generated by Change Analysis Harness from requirement documents, Git diff, and changed-scope evidence. The Harness collects evidence; Codex must still confirm business conclusions, impact, and test design.",
        "",
        "## 1. Analysis metadata",
        "",
        _table(["Field", "Value"], [
            ("Requirement", title),
            ("Requirement URL", request.get("requirement_url")),
            ("Repository", request.get("repository") or repository.get("repository")),
            ("Target branch", request.get("branch") or repository.get("target_branch")),
            ("Base branch", request.get("base_branch") or repository.get("base_branch")),
            ("Merge base", repository.get("merge_base")),
            ("Target commit", repository.get("target_commit")),
            ("Analysis mode", result.get("analysis_mode") or repository.get("analysis_mode")),
            ("Effort", result.get("effort") or (result.get("analysis_input") or {}).get("effort")),
            ("Impact units", len(impact_units)),
            ("Generated on", generated_on),
            ("Harness status", result.get("status")),
        ]),
        "",
        "## 2. Requirement summary",
        "",
        requirement_content or "No requirement content was fetched.",
        "",
        "## 3. Selection and ImpactUnits",
        "",
        (_table(["File", "Status", "AST scopes", "Candidates", "Reasons"], [
            (
                item.get("path"),
                item.get("status"),
                item.get("ast_scope_count"),
                item.get("candidate_reference_count"),
                "; ".join(item.get("reasons") or []),
            )
            for item in selection_files
        ]) if selection_files else "No selection_plan was produced."),
        "",
        (_table(["Unit", "Label", "Files", "Diff bytes", "Guide sections"], [
            (
                item.get("id"),
                item.get("label"),
                len(item.get("files") or []),
                item.get("diff_byte_estimate"),
                ", ".join(item.get("applicable_guide_sections") or []),
            )
            for item in impact_units
        ]) if impact_units else "No impact_units were produced."),
        "",
        "## 4. Change overview",
        "",
        _table(["File", "Change type", "Added", "Deleted"], changed_file_rows),
        "",
        f"- Changed files: **{len(changed_file_rows)}**",
        f"- Changed symbols: **{len(changed_symbols)}**",
        f"- Java AST files parsed: **{(impact.get('java_ast') or {}).get('files_parsed', 0)}**",
        f"- Diff truncated: **{_value(repository.get('diff_truncated_for_analysis'))}**",
        "",
        "## 5. Changed-scope evidence",
        "",
        "### 5.1 Changed symbols",
        "",
        (_table(["Symbol", "Kind", "File", "Line", "Change type"], [
            (item.get("qualified_name") or item.get("symbol"), item.get("kind"), item.get("file"), item.get("line"), item.get("change_type"))
            for item in changed_symbols
        ]) if changed_symbols else "No structured changed symbols were found."),
        "",
        "### 5.2 Java AST analysis",
        "",
        (_table(["File", "Parser", "Scope", "Changed scopes", "Parse errors"], [
            (item.get("path"), item.get("parser"), item.get("analysis_scope"), len(item.get("changed_scopes") or []), len(item.get("parse_errors") or []))
            for item in java_files
        ]) if java_files else "No changed Java files were parsed."),
        "",
        "### 5.3 Impact graph quality",
        "",
        _table(["Metric", "Value"], [
            ("Analysis scope", impact.get("analysis_scope")),
            ("Confirmed edges", graph.get("confirmed_edges", 0)),
            ("Candidate references", graph.get("candidate_references", 0)),
            ("AST calls from changed scopes", graph.get("ast_calls_from_changed_scopes", 0)),
            ("Unresolved symbols", graph.get("unresolved_symbols", 0)),
        ]),
        "",
        "## 6. Candidate references and dependencies",
        "",
        "> These are targeted Harness evidence. `caller_candidates`, `symbol_references`, and similar results are not confirmed call edges until verified against source code.",
        "",
        "### Symbol references",
        "",
        _items(candidate_refs),
        "",
        "### Database dependencies",
        "",
        _items(impact.get("database_dependencies") or []),
        "",
        "### Consumers, callers, and tests",
        "",
        _table(["Category", "Count", "Evidence"], [
            ("Frontend consumers", len(impact.get("frontend_consumers") or []), _items(impact.get("frontend_consumers") or [])),
            ("Backend callers", len(impact.get("backend_callers") or []), _items(impact.get("backend_callers") or [])),
            ("Related tests", len(related_tests), _items(related_tests)),
        ]),
        "",
        "## 7. Risks, limits, and follow-ups",
        "",
    ]
    if warnings:
        lines.extend(["### Harness warnings", "", _items(warnings), ""])
    lines.extend(["### Analysis limits", "", _items(limits), ""])
    lines.extend([
        "### Recommended follow-ups",
        "",
        "- Confirm that requirement acceptance criteria map to the implemented fields and time ranges.",
        "- Confirm database fields, materialized views, ETL, and synchronization jobs are ready in the target environment.",
        "- Confirm NULL handling for numeric expressions in SQL.",
        "- Verify candidate references and API paths with source code, runtime logs, or tests.",
        "- The Harness does not execute tests; add unit, integration, and regression tests for the changed scope.",
        "",
        "## 8. Raw evidence",
        "",
        f"- Full evidence JSON: `{request.get('analysis_output') or 'stdout / in-memory result'}` (JSON pointer: `/repository`)",
        f"- Analysis summary: `{result.get('analysis_input_output') or 'analysis_input in the result'}`",
        f"- Git evidence range: `{change_context.get('source') or impact.get('range') or '-'}`",
        f"- Whole-repository AST scan: **{_value((impact.get('repository_scan') or {}).get('whole_repository_ast_scan'))}**",
        "",
        "---",
        "",
        f"Report generated on {generated_on}.",
        "",
    ])
    return "\n".join(lines)
