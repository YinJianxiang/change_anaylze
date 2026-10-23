"""Match changed file paths to impact-guide section ids."""
from __future__ import annotations

import fnmatch
from typing import Iterable

# Glob patterns are evaluated against POSIX-normalized paths.
GUIDE_MATCH_RULES: list[tuple[str, str]] = [
    ("**/*Controller.java", "api"),
    ("**/*Controller.kt", "api"),
    ("**/controller/**/*.java", "api"),
    ("**/api/**/*.java", "api"),
    ("**/feign/**/*.java", "api"),
    ("**/*Api.java", "api"),
    ("**/*DTO.java", "api"),
    ("**/*VO.java", "api"),
    ("**/*Request.java", "api"),
    ("**/*Response.java", "api"),
    ("**/mapper/**/*.java", "sql"),
    ("**/mapper/**/*.xml", "sql"),
    ("**/dao/**/*.java", "sql"),
    ("**/*Mapper.java", "sql"),
    ("**/*Mapper.xml", "sql"),
    ("**/*.sql", "sql"),
    ("**/application*.yml", "config"),
    ("**/application*.yaml", "config"),
    ("**/application*.properties", "config"),
    ("**/bootstrap*.yml", "config"),
    ("**/bootstrap*.yaml", "config"),
    ("**/*Config.java", "config"),
    ("**/config/**/*.java", "config"),
    ("**/*Job.java", "jobs"),
    ("**/*Scheduler*.java", "jobs"),
    ("**/job/**/*.java", "jobs"),
    ("**/schedule*/**/*.java", "jobs"),
    ("**/*Consumer.java", "messaging"),
    ("**/*Listener.java", "messaging"),
    ("**/mq/**/*.java", "messaging"),
    ("**/kafka/**/*.java", "messaging"),
    ("**/rabbit*/**/*.java", "messaging"),
    ("**/src/test/**/*.java", "test-design"),
    ("**/test/**/*Test.java", "test-design"),
    ("**/*Test.java", "test-design"),
    ("**/*Tests.java", "test-design"),
]

COMMON_SECTION_IDS = (
    "evidence-priority",
    "impact-steps",
    "risk-levels",
    "limits",
)


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _specific_basename_pattern(pattern: str) -> str | None:
    """Return a basename glob only when it encodes more than a bare extension."""
    tail = pattern.rsplit("/", 1)[-1]
    if not tail.startswith("*"):
        return None
    rest = tail[1:]
    if not rest or rest.startswith("."):
        # Reject "*.java" / "*.xml" — too broad for basename-only matching.
        return None
    return tail


def match_section_ids(path: str) -> list[str]:
    normalized = _normalize(path)
    basename = normalized.rsplit("/", 1)[-1]
    matched: list[str] = []
    seen: set[str] = set()
    for pattern, section_id in GUIDE_MATCH_RULES:
        if section_id in seen:
            continue
        basename_pattern = _specific_basename_pattern(pattern)
        if fnmatch.fnmatch(normalized, pattern):
            matched.append(section_id)
            seen.add(section_id)
            continue
        if basename_pattern and fnmatch.fnmatch(basename, basename_pattern):
            matched.append(section_id)
            seen.add(section_id)
    return matched


def applicable_guide_sections(paths: Iterable[str]) -> list[str]:
    ordered = list(COMMON_SECTION_IDS)
    seen = set(ordered)
    for path in paths:
        for section_id in match_section_ids(path):
            if section_id not in seen:
                ordered.append(section_id)
                seen.add(section_id)
    if "test-design" not in seen:
        ordered.append("test-design")
    return ordered
