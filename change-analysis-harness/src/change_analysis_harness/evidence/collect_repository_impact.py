#!/usr/bin/env python3
"""Collect changed-scope impact evidence with Java AST support.

This collector intentionally does not build a general whole-repository call graph.
It parses only changed Java files/lines with tree-sitter, then performs targeted
reference queries for the changed symbols.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from .java_ast import JavaAstUnavailable, merge_java_revisions, parse_changed_java_source
    from .security import is_sensitive_path, redact_sensitive_text
except ImportError:  # pragma: no cover - supports direct script execution
    from java_ast import JavaAstUnavailable, merge_java_revisions, parse_changed_java_source
    from security import is_sensitive_path, redact_sensitive_text


EXCLUDED_PARTS = {
    ".git", "node_modules", "venv", ".venv", "dist", "build", "coverage",
    "__pycache__", "generated", "vendor", "target",
}
TEST_MARKERS = ("test", "tests", "spec", "__tests__", "fixture", "mock")
FRONTEND_MARKERS = ("frontend", "client", "web", "ui", "pages", "components", "views", "src/api")
BACKEND_MARKERS = ("backend", "server", "controller", "service", "repository", "dao", "api", "routes")
DB_MARKERS = ("migration", "migrations", "model", "models", "schema", "repository", "dao", "sql", "mapper")
CONFIG_MARKERS = ("config", "settings", ".env", "properties", "yaml", "yml", "toml")
SCHEDULE_MARKERS = ("cron", "schedule", "scheduled", "celery", "periodic", "job")
MESSAGE_MARKERS = ("consumer", "subscriber", "listener", "queue", "kafka", "rabbit", "message")
SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".properties", ".sql", ".graphql", ".md", ".html", ".vue", ".xml",
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
STOP_SYMBOLS = {
    "if", "for", "while", "return", "print", "str", "int", "list", "dict", "set", "len",
    "get", "post", "put", "delete", "patch", "map", "filter", "require", "import", "super",
    "this", "self", "true", "false", "none", "null", "function", "class", "const", "let", "var",
}


class ImpactError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceState:
    source: str
    old_ref: str | None
    new_ref: str | None
    diff: str


@dataclass
class FileChange:
    old_path: str | None
    new_path: str | None
    old_lines: set[int]
    new_lines: set[int]

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def run_git(repo: Path, *args: str, timeout: int = 60, check: bool = True) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=None,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImpactError(f"git command timed out after {timeout}s") from exc
    except OSError as exc:
        raise ImpactError(f"could not run git: {exc}") from exc
    if check and completed.returncode:
        raise ImpactError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def _safe_evidence(value: str, limit: int = 260) -> str:
    compact = " ".join(redact_sensitive_text(value).strip().split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _excluded(relative: str) -> bool:
    path = Path(relative)
    return is_sensitive_path(path) or any(part.casefold() in EXCLUDED_PARTS for part in path.parts)


def _has_head(repo: Path, timeout: int) -> bool:
    return bool(run_git(repo, "rev-parse", "--verify", "HEAD", timeout=timeout, check=False).strip())


def _source_state(
    repo: Path,
    source: str,
    revision_range: str | None,
    commit: str | None,
    timeout: int,
) -> SourceState:
    common = ["--unified=0", "--no-ext-diff", "--no-textconv", "--find-renames"]
    if source == "range":
        if not revision_range:
            raise ImpactError("--range is required when source=range")
        separator = "..." if "..." in revision_range else ".."
        left, right = revision_range.split(separator, 1)
        old_ref = run_git(repo, "merge-base", left, right, timeout=timeout).strip() if separator == "..." else run_git(repo, "rev-parse", left, timeout=timeout).strip()
        new_ref = run_git(repo, "rev-parse", right, timeout=timeout).strip()
        diff = run_git(repo, "diff", *common, old_ref, new_ref, timeout=timeout)
        return SourceState(source, old_ref, new_ref, diff)
    if source == "commit":
        if not commit:
            raise ImpactError("--commit is required when source=commit")
        new_ref = run_git(repo, "rev-parse", commit, timeout=timeout).strip()
        old_ref = run_git(repo, "rev-parse", f"{new_ref}^", timeout=timeout).strip()
        diff = run_git(repo, "diff", *common, old_ref, new_ref, timeout=timeout)
        return SourceState(source, old_ref, new_ref, diff)
    if source == "staged":
        old_ref = run_git(repo, "rev-parse", "HEAD", timeout=timeout).strip() if _has_head(repo, timeout) else None
        diff = run_git(repo, "diff", "--cached", *common, timeout=timeout)
        return SourceState(source, old_ref, None, diff)
    if source == "working-tree":
        old_ref = run_git(repo, "rev-parse", "HEAD", timeout=timeout).strip() if _has_head(repo, timeout) else None
        if old_ref:
            diff = run_git(repo, "diff", *common, "HEAD", timeout=timeout)
        else:
            diff = run_git(repo, "diff", *common, timeout=timeout) + run_git(repo, "diff", "--cached", *common, timeout=timeout)
        return SourceState(source, old_ref, None, diff)
    raise ImpactError(f"unsupported change source: {source}")


def _parse_changes(diff: str) -> list[FileChange]:
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_line = 0
    new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            if current is not None:
                changes.append(current)
            current = FileChange(None, None, set(), set())
            continue
        if current is None:
            continue
        if raw.startswith("--- "):
            value = raw[4:].split("\t", 1)[0]
            current.old_path = None if value == "/dev/null" else value[2:] if value.startswith("a/") else value
            continue
        if raw.startswith("+++ "):
            value = raw[4:].split("\t", 1)[0]
            current.new_path = None if value == "/dev/null" else value[2:] if value.startswith("b/") else value
            continue
        if raw.startswith("@@"):
            match = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
            if match:
                old_line = int(match.group(1))
                new_line = int(match.group(3))
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            current.old_lines.add(old_line)
            old_line += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            current.new_lines.add(new_line)
            new_line += 1
        elif not raw.startswith("\\"):
            old_line += 1
            new_line += 1
    if current is not None:
        changes.append(current)
    return [item for item in changes if item.path and not _excluded(item.path)]


def _read_file(repo: Path, state: SourceState, path: str | None, side: str, timeout: int) -> str | None:
    if not path or _excluded(path):
        return None
    if side == "old":
        if not state.old_ref:
            return None
        value = run_git(repo, "show", f"{state.old_ref}:{path}", timeout=timeout, check=False)
        return value if value else None
    if state.source in {"range", "commit"}:
        if not state.new_ref:
            return None
        value = run_git(repo, "show", f"{state.new_ref}:{path}", timeout=timeout, check=False)
        return value if value else None
    if state.source == "staged":
        value = run_git(repo, "show", f":{path}", timeout=timeout, check=False)
        return value if value else None
    file_path = repo / path
    if not file_path.is_file():
        return None
    return file_path.read_text(encoding="utf-8", errors="replace")


def _changed_line_symbols(path: str, source: str, changed_lines: set[int], revision: str) -> list[dict[str, Any]]:
    suffix = Path(path).suffix.lower()
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("class", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")),
        ("function", re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("function", re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
        ("method", re.compile(r"^\s*(?:public|private|protected|static|final|async|override|synchronized|abstract|native|\s)*[\w<>,.?\[\]]+\s+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|throws\b)")),
    ]
    if suffix == ".py":
        patterns = patterns[:1] + patterns[1:2]
    lines = source.splitlines()
    result: list[dict[str, Any]] = []
    for line_number in sorted(changed_lines):
        if not (0 < line_number <= len(lines)):
            continue
        line = lines[line_number - 1]
        for kind, pattern in patterns:
            match = pattern.search(line)
            if match:
                symbol = match.group(1)
                result.append({
                    "symbol": symbol,
                    "qualified_name": symbol,
                    "kind": kind,
                    "file": path,
                    "line": line_number,
                    "start_line": line_number,
                    "end_line": line_number,
                    "changed_lines": [line_number],
                    "evidence": _safe_evidence(line),
                    "revision": revision,
                    "parser": "changed-line-fallback",
                })
                break
    return result


def _changed_line_calls(path: str, source: str, changed_lines: set[int], revision: str) -> list[dict[str, Any]]:
    lines = source.splitlines()
    result: list[dict[str, Any]] = []
    for line_number in sorted(changed_lines):
        if not (0 < line_number <= len(lines)):
            continue
        line = lines[line_number - 1]
        for candidate in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", line):
            if candidate.casefold() in STOP_SYMBOLS:
                continue
            result.append({
                "caller": f"{path}::<changed-line>@{line_number}",
                "callee_symbol": candidate,
                "receiver": None,
                "line": line_number,
                "evidence": _safe_evidence(line),
                "call_type": "changed_line_call",
                "revision": revision,
                "parser": "changed-line-fallback",
            })
    unique = {(item["callee_symbol"], item["line"], item["revision"]): item for item in result}
    return list(unique.values())


def _reference_type(path: str, line: str, symbol: str, changed_paths: set[str]) -> str:
    lowered = path.casefold()
    if any(marker in lowered for marker in TEST_MARKERS):
        return "related_test"
    if path in changed_paths:
        return "changed_file_reference"
    if re.search(rf"\b{re.escape(symbol)}\s*\(", line):
        return "direct_caller_candidate"
    if re.search(r"(?:/api/|https?://|@(?:Get|Post|Put|Delete|Patch)Mapping)", line, re.IGNORECASE):
        return "api_reference"
    return "symbol_reference"


def _parse_grep_line(raw: str, ref: str | None) -> tuple[str, int, str] | None:
    value = raw
    if ref and value.startswith(ref + ":"):
        value = value[len(ref) + 1:]
    match = re.match(r"(.+?):(\d+):(.*)$", value)
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _targeted_references(
    repo: Path,
    state: SourceState,
    symbols: list[str],
    changed_paths: set[str],
    *,
    max_results: int,
    max_per_symbol: int,
    timeout: int,
) -> tuple[list[dict[str, Any]], list[str], bool, int]:
    references: list[dict[str, Any]] = []
    unresolved: list[str] = []
    truncated = False
    queries = 0
    target_ref = state.new_ref if state.source in {"range", "commit"} else None
    for symbol in symbols:
        if len(symbol) < 3 or not IDENTIFIER_RE.match(symbol):
            continue
        args = ["grep", "-n", "-I", "-F", "-e", symbol]
        if target_ref:
            args.extend([target_ref, "--"])
        else:
            args.append("--")
        output = run_git(repo, *args, timeout=timeout, check=False)
        queries += 1
        found = 0
        for raw in output.splitlines():
            parsed = _parse_grep_line(raw, target_ref)
            if not parsed:
                continue
            path, line_number, evidence = parsed
            if _excluded(path):
                continue
            references.append({
                "symbol": symbol,
                "file": path,
                "line": line_number,
                "evidence": _safe_evidence(evidence),
                "reference_type": _reference_type(path, evidence, symbol, changed_paths),
                "source": f"git-grep:{target_ref or 'working-tree'}",
            })
            found += 1
            if found >= max_per_symbol or len(references) >= max_results:
                truncated = True
                break
        if not found:
            unresolved.append(symbol)
        if len(references) >= max_results:
            break
    unique = {(item["symbol"], item["file"], item["line"], item["reference_type"]): item for item in references}
    return list(unique.values()), unresolved, truncated, queries


def _has_marker(item: dict[str, Any], markers: tuple[str, ...]) -> bool:
    value = f"{item.get('file', '')}/{item.get('evidence', '')}".casefold()
    return any(marker in value for marker in markers)


def collect_impact(
    repo: Path,
    revision_range: str | None = None,
    *,
    source: str | None = None,
    commit: str | None = None,
    max_results: int = 500,
    max_per_object: int = 30,
    timeout: int = 60,
) -> dict[str, Any]:
    root = Path(run_git(repo, "rev-parse", "--show-toplevel", timeout=timeout).strip())
    effective_source = source or ("range" if revision_range else "working-tree")
    state = _source_state(root, effective_source, revision_range, commit, timeout)
    changes = _parse_changes(state.diff)
    changed_paths = {item.path for item in changes}

    java_files: list[dict[str, Any]] = []
    changed_line_scopes: list[dict[str, Any]] = []
    all_calls: list[dict[str, Any]] = []
    warnings: list[str] = []
    ast_files_parsed = 0

    for change in changes:
        path = change.path
        suffix = Path(path).suffix.lower()
        if suffix not in SOURCE_SUFFIXES:
            continue
        old_source = _read_file(root, state, change.old_path, "old", timeout)
        new_source = _read_file(root, state, change.new_path, "new", timeout)
        if suffix == ".java":
            try:
                old_result = parse_changed_java_source(
                    path=change.old_path or path,
                    source_text=old_source or "",
                    changed_lines=change.old_lines,
                    revision=state.old_ref or "base",
                )
                new_result = parse_changed_java_source(
                    path=change.new_path or path,
                    source_text=new_source or "",
                    changed_lines=change.new_lines,
                    revision=state.new_ref or effective_source,
                )
                java_file = merge_java_revisions(path=path, old_result=old_result, new_result=new_result)
                java_files.append(java_file)
                all_calls.extend(java_file["calls_from_changed_scopes"])
                warnings.extend(java_file["parse_errors"])
                ast_files_parsed += 1
            except JavaAstUnavailable as exc:
                warnings.append(str(exc))
        else:
            if old_source:
                changed_line_scopes.extend(_changed_line_symbols(change.old_path or path, old_source, change.old_lines, state.old_ref or "base"))
                all_calls.extend(_changed_line_calls(change.old_path or path, old_source, change.old_lines, state.old_ref or "base"))
            if new_source:
                changed_line_scopes.extend(_changed_line_symbols(change.new_path or path, new_source, change.new_lines, state.new_ref or effective_source))
                all_calls.extend(_changed_line_calls(change.new_path or path, new_source, change.new_lines, state.new_ref or effective_source))

    changed_symbols: list[dict[str, Any]] = []
    for java_file in java_files:
        for scope in java_file["changed_scopes"]:
            changed_symbols.append({
                "symbol": scope.get("symbol"),
                "qualified_name": scope.get("qualified_name"),
                "kind": scope.get("kind"),
                "file": java_file["path"],
                "line": scope.get("start_line"),
                "start_line": scope.get("start_line"),
                "end_line": scope.get("end_line"),
                "changed_lines": (scope.get("new_scope") or scope.get("old_scope") or {}).get("changed_lines", []),
                "evidence": scope.get("signature", ""),
                "change_type": scope.get("change_type"),
                "parser": "tree-sitter-java",
            })
    changed_symbols.extend({**item, "change_type": "modified"} for item in changed_line_scopes)
    unique_symbols = {
        (item.get("file"), item.get("qualified_name"), item.get("kind"), item.get("change_type")): item
        for item in changed_symbols
        if item.get("symbol")
    }
    changed_symbols = list(unique_symbols.values())

    callee_symbols = sorted({
        str(item.get("callee_symbol")) for item in all_calls
        if IDENTIFIER_RE.match(str(item.get("callee_symbol", ""))) and str(item.get("callee_symbol", "")).casefold() not in STOP_SYMBOLS
    })
    seed_symbols = sorted({
        str(item["symbol"]) for item in changed_symbols
        if IDENTIFIER_RE.match(str(item.get("symbol", "")))
    })
    references, unresolved, truncated, queries = _targeted_references(
        root,
        state,
        seed_symbols,
        changed_paths,
        max_results=max(max_results, 1),
        max_per_symbol=max(max_per_object, 1),
        timeout=max(timeout, 1),
    )

    related_tests = [item for item in references if item["reference_type"] == "related_test"]
    api_references = [item for item in references if item["reference_type"] == "api_reference"]
    frontend = [item for item in references if _has_marker(item, FRONTEND_MARKERS)]
    backend = [item for item in references if _has_marker(item, BACKEND_MARKERS)]
    database = [item for item in references if _has_marker(item, DB_MARKERS)]
    configuration = [item for item in references if _has_marker(item, CONFIG_MARKERS)]
    scheduled = [item for item in references if _has_marker(item, SCHEDULE_MARKERS)]
    consumers = [item for item in references if _has_marker(item, MESSAGE_MARKERS)]

    scan_complete = not warnings and not truncated
    limits = [
        "只对 Git diff 命中的文件和变更行执行结构分析，不再构建全仓库通用调用图。",
        "Java 使用 tree-sitter AST 定位变更类、方法、构造器及这些变更作用域内的调用。",
        "仓库其他位置仅通过 changed symbol 的定向 git grep 获取候选引用；候选引用不等同于已确认调用边。",
        "动态分派、反射、配置驱动调用、生成代码和跨仓库调用仍需 Codex 结合源码确认。",
    ]
    if truncated:
        limits.append("定向引用结果达到配置上限，结果已截断。")
    limits.extend(warnings)

    caller_candidates = [item for item in references if item["reference_type"] == "direct_caller_candidate"]
    impact_slice = {
        "change_seeds": changed_symbols,
        "java_ast_files": java_files,
        "calls_from_changed_scopes": all_calls,
        "caller_candidates": caller_candidates,
        "related_tests": related_tests,
        "downstream_effects": {
            "frontend": frontend,
            "database": database,
            "configuration": configuration,
            "scheduled": scheduled,
            "messages": consumers,
        },
        "graph_quality": {
            "usable": bool(changed_symbols) and not warnings,
            "analysis_scope": "changed_files_and_changed_ast_scopes_only",
            "confirmed_edges": 0,
            "candidate_references": len(references),
            "ast_calls_from_changed_scopes": len(all_calls),
            "unresolved_symbols": len(unresolved),
        },
    }
    return {
        "repository": str(root),
        "source": effective_source,
        "range": revision_range,
        "commit": commit,
        "base_revision": state.old_ref,
        "target_revision": state.new_ref,
        "analysis_mode": "Changed-scope AST analysis",
        "analysis_scope": "changed_files_and_changed_ast_scopes_only",
        "whole_repository_analysis": False,
        "changed_files": [
            {
                "path": item.path,
                "old_path": item.old_path,
                "new_path": item.new_path,
                "old_changed_lines": sorted(item.old_lines),
                "new_changed_lines": sorted(item.new_lines),
            }
            for item in changes
        ],
        "changed_symbols": changed_symbols,
        "changed_callees": callee_symbols,
        "java_ast": {
            "available": not any("tree-sitter" in warning and "需要" in warning for warning in warnings),
            "files": java_files,
            "files_parsed": ast_files_parsed,
        },
        "repository_scan": {
            # Keep scan_scope for consumers of the first harness schema.
            # The explicit fields remove the ambiguity that targeted grep can
            # inspect other files without turning them into AST scan inputs.
            "scan_scope": "changed_files_only",
            "ast_scan_scope": "changed_files_only",
            "reference_search_scope": "targeted_changed_symbols_only",
            "changed_files_considered": len(changes),
            "java_ast_files_parsed": ast_files_parsed,
            "targeted_reference_queries": queries,
            "targeted_references_found": len(references),
            "whole_repository_ast_scan": False,
            "scan_complete": scan_complete,
            "parse_failures": warnings,
        },
        "impact_slice": impact_slice,
        "symbol_references": references,
        "api_references": api_references,
        "frontend_consumers": frontend,
        "backend_callers": backend,
        "database_dependencies": database,
        "configuration_references": configuration,
        "scheduled_jobs": scheduled,
        "message_consumers": consumers,
        "related_tests": related_tests,
        "unresolved_symbols": unresolved,
        "search_truncated": truncated,
        "analysis_limits": limits,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Complete repository path")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--working-tree", action="store_true", help="Analyze tracked working-tree changes (default)")
    source.add_argument("--staged", action="store_true", help="Analyze staged changes only")
    source.add_argument("--commit", metavar="REV", help="Analyze one commit")
    source.add_argument("--range", dest="revision_range", metavar="BASE...HEAD", help="Analyze a Git range")
    parser.add_argument("--max-results", type=int, default=500)
    parser.add_argument("--max-per-object", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source = "range" if args.revision_range else "commit" if args.commit else "staged" if args.staged else "working-tree"
        result = collect_impact(
            Path(args.repo).expanduser().resolve(),
            args.revision_range,
            source=source,
            commit=args.commit,
            max_results=max(args.max_results, 1),
            max_per_object=max(args.max_per_object, 1),
            timeout=max(args.timeout, 1),
        )
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (ImpactError, OSError, ValueError) as exc:
        json.dump(
            {
                "error": str(exc),
                "analysis_mode": "Diff-only analysis",
                "analysis_limits": ["变更作用域证据采集失败，只能退化为 diff 分析。"],
            },
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
