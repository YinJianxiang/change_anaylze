"""Canonical interpretation rules and report template for the analysis Skill."""
from __future__ import annotations

import re
from importlib.resources import files
from typing import Iterable

_SECTION_RE = re.compile(r"<!--\s*section:([a-z0-9-]+)\s*-->", re.IGNORECASE)


def _guide_root():
    return files("change_analysis_harness").joinpath("guidance")


def _parse_sections(markdown: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(markdown))
    if not matches:
        # The user-maintained guide stays verbatim; compatibility ids select its
        # existing headings instead of injecting Harness-only rules into it.
        headings = list(re.finditer(r"^## (.+)$", markdown, re.MULTILINE))
        by_heading = {
            match.group(1).strip(): markdown[
                match.start():headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            ].strip()
            for index, match in enumerate(headings)
        }
        aliases = {
            "evidence-priority": "Coverage rules",
            "impact-steps": "Change-to-test heuristics",
            "risk-levels": "Risk level",
            "api": "Change-to-test heuristics",
            "sql": "Change-to-test heuristics",
            "config": "Change-to-test heuristics",
            "jobs": "Change-to-test heuristics",
            "messaging": "Change-to-test heuristics",
            "test-design": "Scenario quality",
            "limits": "Coverage rules",
        }
        sections = {"full": markdown.strip(), **{
            key: by_heading[heading] for key, heading in aliases.items() if heading in by_heading
        }}
        completeness = by_heading.get("Requirement completeness (Mode B)")
        if completeness and "evidence-priority" in sections:
            sections["evidence-priority"] += "\n\n" + completeness
        return sections
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[match.group(1)] = markdown[start:end].strip()
    return sections


def load_guide_sections(section_ids: Iterable[str] | None = None) -> str:
    """Load selected sections from the impact guide, preserving declaration order."""
    markdown = _guide_root().joinpath("impact-and-test-guide.md").read_text(encoding="utf-8")
    sections = _parse_sections(markdown)
    if section_ids is None:
        return markdown
    wanted = [str(item).strip() for item in section_ids if str(item).strip()]
    if not wanted:
        return markdown
    chunks = []
    for section_id in wanted:
        body = sections.get(section_id)
        if body:
            chunks.append(f"## Guide section: {section_id}\n\n{body}")
    return "\n\n".join(chunks) if chunks else markdown


def load_analysis_guidance(section_ids: Iterable[str] | None = None) -> str:
    root = _guide_root()
    guide = load_guide_sections(section_ids)
    report_template = root.joinpath("report-template.md").read_text(encoding="utf-8")
    guide_label = (
        "impact-and-test-guide.md (selected sections)"
        if section_ids is not None
        else "impact-and-test-guide.md"
    )
    return (
        f"\n\n# Bundled Skill reference: {guide_label}\n\n{guide}"
        f"\n\n# Bundled Skill reference: report-template.md\n\n{report_template}"
    )
