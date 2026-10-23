"""Deterministic diff hints; matches are candidates, never confirmed defects."""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import PurePosixPath


RULES = (
    ("SEC001", "high", r'''(?i)\b(?:api_key|secret|token|password|passwd|client_secret)\s*[:=]\s*['"][^'"\s]{8,}['"]''', "Possible literal credential; verify and move credentials to secret storage."),
    ("SEC002", "high", r"\b(?:eval|exec)\s*\(", "Dynamic execution; verify input trust and alternatives."),
    ("SEC003", "high", r'''(?i)['"]\s*(?:select|insert|update|delete)\b.*['"]\s*\+''', "Possible SQL concatenation; verify parameter binding."),
    ("SEC004", "high", r"\bsubprocess\.\w+\(.*\bshell\s*=\s*True", "Shell invocation; verify input validation."),
    ("ERR001", "medium", r"\b(?:except\s+(?:Exception|BaseException)\b|catch\s*\(\s*(?:Exception|Throwable)\b|except\s*:)", "Broad exception handler; verify logging and propagation."),
    ("OPS001", "medium", r"\b(?:shutil\.rmtree|os\.remove|Path\([^)]*\)\.unlink)\s*\(", "Deletion operation; verify allowed paths."),
    ("DBG001", "low", r"\b(?:console\.log|System\.out\.println|print)\s*\(", "Debug output; verify production logging policy."),
    ("TST001", "low", r"@(?:Ignore|Disabled)\b|\bpytest\.mark\.skip\b", "Skipped test; verify reason and coverage."),
)
COMPILED = [(key, severity, re.compile(pattern), message) for key, severity, pattern, message in RULES]
RANK = {"low": 1, "medium": 2, "high": 3}


def _path(value: str) -> str:
    if value.startswith('"'):
        value = ast.literal_eval(value)
        try:
            value = value.encode("latin1").decode("utf-8")
        except (UnicodeError, ValueError):
            pass
    return value[2:] if value.startswith(("a/", "b/")) else value


def scan_diff(diff: str, *, exclude: tuple[str, ...] = ()) -> dict:
    """Scan added lines only; exclusions match exact directory segments on either side.

    No source snippets are returned, so literal credentials are not duplicated in hints.
    The original diff remains evidence and must be handled accordingly.
    """
    findings, files, excluded = [], [], []
    path = old_path = ""
    line = remaining_old = remaining_new = 0
    active = False
    excluded_segments = {part.casefold() for part in exclude}

    def is_excluded() -> bool:
        return any(segment.casefold() in excluded_segments
                   for name in (path, old_path) for segment in PurePosixPath(name).parts[:-1])

    def record_file() -> None:
        target = excluded if is_excluded() else files
        if path not in target:
            target.append(path)

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            if active:
                raise ValueError("Incomplete unified diff hunk before next file")
            active = False
            parts = shlex.split(raw[len("diff --git "):], posix=False)
            if len(parts) != 2:
                # Git leaves ordinary spaces unquoted; split at the b/ boundary.
                pair = raw[len("diff --git "):].split(" b/", 1)
                if len(pair) != 2:
                    raise ValueError("Unsupported diff header; expected two Git paths")
                parts = [pair[0], "b/" + pair[1]]
            old_path, path = map(_path, parts)
            record_file()
        elif not active and raw.startswith("--- "):
            old_path = _path(raw[4:].split("\t", 1)[0])
        elif not active and raw.startswith("+++ "):
            new_path = _path(raw[4:].split("\t", 1)[0])
            path = old_path if new_path == "/dev/null" else new_path
            record_file()
        elif raw.startswith("@@ "):
            if active:
                raise ValueError("Incomplete unified diff hunk before next hunk")
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if not match or not path:
                raise ValueError("Invalid unified diff hunk")
            remaining_old = int(match[2] or 1)
            line, remaining_new = int(match[3]), int(match[4] or 1)
            active = bool(remaining_old or remaining_new)
        elif active and raw.startswith(("+", "-", " ")):
            blocked = is_excluded()
            if raw.startswith("+"):
                if not blocked:
                    for key, severity, pattern, message in COMPILED:
                        if pattern.search(raw[1:]):
                            findings.append({"rule_id": key, "severity": severity,
                                             "file": path, "line": line,
                                             "message": message, "status": "candidate"})
                line += 1
                remaining_new -= 1
            elif raw.startswith("-"):
                remaining_old -= 1
            else:
                line += 1
                remaining_old -= 1
                remaining_new -= 1
            if min(remaining_old, remaining_new) < 0:
                raise ValueError("Diff hunk exceeds declared line counts")
            active = bool(remaining_old or remaining_new)
    if active:
        raise ValueError("Incomplete unified diff hunk")
    return {"engine": "diff-rules-v1", "status": "completed", "findings": findings,
            "summary": {severity: sum(f["severity"] == severity for f in findings) for severity in RANK},
            "scanned_files": files, "excluded_files": excluded,
            "limitations": ["Added-line patterns only; no data-flow or semantic validation.",
                            "No matches does not mean no defects. Exclusions apply only to this risk scan."]}


def gate_failed(scan: dict, threshold: str | None) -> bool:
    return threshold is not None and any(RANK[f["severity"]] >= RANK[threshold] for f in scan["findings"])
