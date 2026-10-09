"""Shared interpretation rules, independent of Codex and bot execution."""
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
        return {"full": markdown.strip()}
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
