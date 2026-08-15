#!/usr/bin/env python3
"""Scan repository source, build a lightweight call graph, and collect change impact evidence."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


EXCLUDED_PARTS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    "generated",
    "vendor",
}
SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".properties", ".sql", ".graphql", ".md", ".html", ".vue",
}
GRAPH_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".vue"}
TEST_MARKERS = ("test", "tests", "spec", "__tests__", "fixture", "mock")
FRONTEND_MARKERS = ("frontend", "client", "web", "ui", "pages", "components", "views", "src/api")
BACKEND_MARKERS = ("backend", "server", "controller", "service", "repository", "dao", "api", "routes")
DB_MARKERS = ("migration", "migrations", "model", "models", "schema", "repository", "dao", "sql")
CONFIG_MARKERS = ("config", "settings", ".env", "properties", "yaml", "yml", "toml")
SCHEDULE_MARKERS = ("cron", "schedule", "scheduled", "celery", "periodic", "job")
MESSAGE_MARKERS = ("consumer", "subscriber", "listener", "queue", "kafka", "rabbit", "message")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
STOP_SYMBOLS = {
    "if", "for", "while", "return", "print", "str", "int", "list", "dict", "set", "len",
    "get", "post", "put", "delete", "patch", "map", "filter", "require", "import", "super",
    "this", "self", "true", "false", "none", "null", "function", "class", "const", "let", "var",
}


class ImpactError(RuntimeError):
    """Raised when repository impact evidence cannot be collected."""


@dataclass(frozen=True)
class ChangedSymbol:
    symbol: str
    kind: str
    file: str
    line: int
    evidence: str
    change_type: str = "modified"


@dataclass(frozen=True)
class GraphNode:
    id: str
    symbol: str
    kind: str
    file: str
    line: int
    changed: bool


@dataclass(frozen=True)
class CallSite:
    caller: str
    callee_symbol: str
    file: str
    line: int
    evidence: str
    parser: str


def run_git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        process = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImpactError(f"git command timed out after {timeout}s") from exc
    except OSError as exc:
        raise ImpactError(f"could not run git: {exc}") from exc
    if process.returncode:
        raise ImpactError(process.stderr.strip() or f"git {' '.join(args)} failed")
    return process.stdout


def _safe_evidence(line: str, limit: int = 240) -> str:
    compact = " ".join(line.strip().split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def iter_searchable_files(repo: Path, max_file_bytes: int, max_files: int) -> Iterable[Path]:
    emitted = 0
    for directory, child_directories, filenames in os.walk(repo):
        child_directories[:] = [name for name in child_directories if name not in EXCLUDED_PARTS]
        for filename in filenames:
            path = Path(directory) / filename
            try:
                if path.suffix.lower() not in SOURCE_SUFFIXES or path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            yield path
            emitted += 1
            if emitted >= max_files:
                return


def _definition_patterns(suffix: str) -> list[tuple[str, re.Pattern[str]]]:
    common = [
        ("class", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("method", re.compile(r"^\s*(?:public|private|protected|static|final|async|override|synchronized|abstract|native|\s)*[\w<>,.?\[\]]+\s+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|throws\b)")),
        ("function", re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>")),
    ]
    if suffix == ".py":
        return [
            ("class", re.compile(r"^\s*class\s+([A-Za-z_]\w*)")),
            ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")),
        ]
    return common


def _api_objects(line: str, file: str, line_number: int) -> list[ChangedSymbol]:
    objects: list[ChangedSymbol] = []
    patterns = (
        re.compile(r"@(?:\w+\.)?(get|post|put|patch|delete|options|head)\s*\(\s*['\"]([^'\"]+)"),
        re.compile(r"\b(?:router|app)\.(get|post|put|patch|delete|options|head)\s*\(\s*['\"]([^'\"]+)"),
        re.compile(r"\b(fetch)\s*\(\s*[`'\"]([^`'\"]+)"),
        re.compile(r"\b(?:axios\.)?(get|post|put|patch|delete)\s*\(\s*[`'\"]([^`'\"]+)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(line):
            method = match.group(1).upper()
            if method == "FETCH":
                method = "UNKNOWN"
            objects.append(ChangedSymbol(match.group(2), f"api:{method}", file, line_number, _safe_evidence(line)))
    return objects


def _domain_objects(line: str, file: str, line_number: int) -> list[ChangedSymbol]:
    objects: list[ChangedSymbol] = []
    patterns = (
        ("environment_variable", re.compile(r"(?:os\.(?:getenv|environ\.get)|process\.env\.)\s*\(?\s*['\"]?([A-Z][A-Z0-9_]{2,})")),
        ("database_table", re.compile(r"(?:__tablename__\s*=|\b(?:from|join|update|into|table)\s+)\s*[`'\"]?([A-Za-z_][\w.]*)", re.IGNORECASE)),
        ("scheduled_job", re.compile(r"@(?:cron|scheduled|scheduler|periodic_task)\b|\bcron\s*\(" , re.IGNORECASE)),
        ("message_consumer", re.compile(r"@(?:consumer|listener|subscriber)\b|\b(?:consume|subscribe)\s*\(", re.IGNORECASE)),
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(line):
            symbol = match.group(1) if match.lastindex else _safe_evidence(line, 80)
            objects.append(ChangedSymbol(symbol, kind, file, line_number, _safe_evidence(line)))
    return objects


def _changed_line_numbers(diff: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            changed.setdefault(current, set())
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                new_line = int(match.group(1))
        elif current and line.startswith("+") and not line.startswith("+++"):
            changed[current].add(new_line)
            new_line += 1
        elif current and line.startswith("-") and not line.startswith("---"):
            continue
        elif current and not line.startswith("\\"):
            new_line += 1
    return changed


def _removed_objects(diff: str) -> tuple[list[ChangedSymbol], set[str]]:
    objects: list[ChangedSymbol] = []
    callees: set[str] = set()
    current = ""
    old_line = 0
    suffix = ""
    for line in diff.splitlines():
        if line.startswith("--- a/"):
            current = line[6:]
            suffix = Path(current).suffix.lower()
        elif line.startswith("@@"):
            match = re.search(r"-(\d+)(?:,(\d+))?", line)
            if match:
                old_line = int(match.group(1))
        elif current and line.startswith("-") and not line.startswith("---"):
            content = line[1:]
            for kind, pattern in _definition_patterns(suffix):
                match = pattern.search(content)
                if match:
                    objects.append(
                        ChangedSymbol(
                            match.group(1), kind, current, old_line, _safe_evidence(content), "deleted"
                        )
                    )
                    break
            objects.extend(
                ChangedSymbol(item.symbol, item.kind, item.file, item.line, item.evidence, "deleted")
                for item in _api_objects(content, current, old_line)
            )
            objects.extend(
                ChangedSymbol(item.symbol, item.kind, item.file, item.line, item.evidence, "deleted")
                for item in _domain_objects(content, current, old_line)
            )
            for candidate in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", content):
                if candidate.lower() not in STOP_SYMBOLS and IDENTIFIER_RE.match(candidate):
                    callees.add(candidate)
            old_line += 1
        elif current and line.startswith("+") and not line.startswith("+++"):
            continue
        elif current and not line.startswith("\\"):
            old_line += 1
    return objects, callees


def extract_changed_objects(repo: Path, revision_range: str, timeout: int) -> tuple[list[ChangedSymbol], list[str]]:
    diff = run_git(repo, "diff", "--unified=0", "--no-ext-diff", "--no-textconv", revision_range, timeout=timeout)
    changed_lines = _changed_line_numbers(diff)
    objects, callees = _removed_objects(diff)
    for relative, line_numbers in changed_lines.items():
        path = repo / relative
        if not path.is_file() or _excluded(Path(relative)) or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        patterns = _definition_patterns(path.suffix.lower())
        enclosing: ChangedSymbol | None = None
        python_scopes: list[tuple[int, ChangedSymbol]] = []
        for index, line in enumerate(lines, 1):
            indentation = len(line) - len(line.lstrip())
            if path.suffix.lower() == ".py" and line.strip() and not line.lstrip().startswith(("#", "@")):
                while python_scopes and indentation <= python_scopes[-1][0]:
                    python_scopes.pop()
            for kind, pattern in patterns:
                match = pattern.search(line)
                if match:
                    enclosing = ChangedSymbol(match.group(1), kind, relative, index, _safe_evidence(line))
                    if path.suffix.lower() == ".py":
                        python_scopes.append((indentation, enclosing))
                    if index in line_numbers:
                        objects.append(enclosing)
                    break
            if index not in line_numbers:
                continue
            if path.suffix.lower() == ".py":
                objects.extend(item for _, item in python_scopes)
            elif enclosing:
                objects.append(enclosing)
            objects.extend(_api_objects(line, relative, index))
            objects.extend(_domain_objects(line, relative, index))
            for candidate in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", line):
                if candidate.lower() not in STOP_SYMBOLS and IDENTIFIER_RE.match(candidate):
                    callees.add(candidate)
    unique = {
        (item.symbol, item.kind, item.file, item.line, item.change_type): item for item in objects
    }
    return sorted(unique.values(), key=lambda item: (item.file, item.line, item.symbol)), sorted(callees)


def _reference_type(path: str, line: str, symbol: str) -> str:
    lowered = path.lower()
    if any(marker in lowered for marker in TEST_MARKERS):
        return "related_test"
    if re.search(rf"\b(?:class|def|function)\s+{re.escape(symbol)}\b", line):
        return "declaration"
    return "direct_caller"


def _reference(symbol: str, path: str, line_number: int, line: str, reference_type: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "file": path,
        "line": line_number,
        "reference_type": reference_type,
        "evidence": _safe_evidence(line),
    }


def _has_marker(path: str, evidence: str, markers: tuple[str, ...]) -> bool:
    value = f"{path} {evidence}".lower()
    return any(marker in value for marker in markers)


def _ast_call_symbol(node: ast.Call) -> str | None:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


class _PythonGraphVisitor(ast.NodeVisitor):
    def __init__(
        self,
        relative: str,
        lines: list[str],
        changed_symbols: set[str],
        max_nodes: int,
        max_calls: int,
        deadline: float,
    ) -> None:
        self.relative = relative
        self.lines = lines
        self.changed_symbols = changed_symbols
        self.max_nodes = max_nodes
        self.max_calls = max_calls
        self.deadline = deadline
        self.nodes: dict[str, GraphNode] = {}
        self.calls: list[CallSite] = []
        self.class_stack: list[str] = []
        self.function_stack: list[tuple[str, str]] = []
        self.truncated = False

    def _add_node(self, symbol: str, kind: str, line: int, qualifier: str) -> str:
        node_id = f"{self.relative}::{qualifier}@{line}"
        if len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return node_id
        self.nodes[node_id] = GraphNode(
            node_id, symbol, kind, self.relative, line, symbol in self.changed_symbols
        )
        return node_id

    def _module_node(self) -> str:
        node_id = f"{self.relative}::<module>@1"
        if node_id not in self.nodes and len(self.nodes) < self.max_nodes:
            self.nodes[node_id] = GraphNode(
                node_id, "<module>", "module", self.relative, 1, False
            )
        return node_id

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualifier = ".".join([*self.class_stack, node.name])
        self._add_node(node.name, "class", node.lineno, qualifier)
        self.class_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prefix = [*self.class_stack, *(item[0] for item in self.function_stack)]
        qualifier = ".".join([*prefix, node.name])
        kind = "method" if self.class_stack else "function"
        node_id = self._add_node(node.name, kind, node.lineno, qualifier)
        self.function_stack.append((node.name, node_id))
        for statement in node.body:
            self.visit(statement)
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if time.monotonic() >= self.deadline or len(self.calls) >= self.max_calls:
            self.truncated = True
            return
        symbol = _ast_call_symbol(node)
        if symbol and symbol.lower() not in STOP_SYMBOLS:
            caller = self.function_stack[-1][1] if self.function_stack else self._module_node()
            evidence = self.lines[node.lineno - 1] if 0 < node.lineno <= len(self.lines) else ""
            self.calls.append(
                CallSite(caller, symbol, self.relative, node.lineno, _safe_evidence(evidence), "python-ast")
            )
        self.generic_visit(node)


def _scan_python_graph(
    path: Path,
    relative: str,
    changed_symbols: set[str],
    max_nodes: int,
    max_calls: int,
    deadline: float,
) -> tuple[list[GraphNode], list[CallSite], bool, bool]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=relative)
    except (OSError, SyntaxError, ValueError):
        return [], [], False, True
    visitor = _PythonGraphVisitor(
        relative,
        source.splitlines(),
        changed_symbols,
        max_nodes,
        max_calls,
        deadline,
    )
    visitor.visit(tree)
    return list(visitor.nodes.values()), visitor.calls, visitor.truncated, False


def _generic_definition(line: str) -> tuple[str, str] | None:
    patterns = (
        ("class", re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("function", re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
        ("method", re.compile(r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|final|async|override|synchronized|abstract)\s+)*[\w<>,.?\[\]]+\s+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|throws\b)")),
    )
    for kind, pattern in patterns:
        match = pattern.search(line)
        if match and match.group(1).lower() not in STOP_SYMBOLS:
            return kind, match.group(1)
    return None


def _scan_generic_graph(
    path: Path,
    relative: str,
    changed_symbols: set[str],
    max_nodes: int,
    max_calls: int,
    deadline: float,
) -> tuple[list[GraphNode], list[CallSite], bool, bool]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], [], False, True
    nodes: dict[str, GraphNode] = {}
    calls: list[CallSite] = []
    scopes: list[tuple[str, int]] = []
    brace_depth = 0
    truncated = False

    def module_node() -> str:
        node_id = f"{relative}::<module>@1"
        if node_id not in nodes and len(nodes) < max_nodes:
            nodes[node_id] = GraphNode(node_id, "<module>", "module", relative, 1, False)
        return node_id

    for line_number, line in enumerate(lines, 1):
        if time.monotonic() >= deadline:
            truncated = True
            break
        while scopes and brace_depth <= scopes[-1][1]:
            scopes.pop()
        definition = _generic_definition(line)
        definition_symbol = definition[1] if definition else None
        line_caller: str | None = None
        if definition:
            kind, symbol = definition
            node_id = f"{relative}::{symbol}@{line_number}"
            if len(nodes) < max_nodes:
                nodes[node_id] = GraphNode(
                    node_id, symbol, kind, relative, line_number, symbol in changed_symbols
                )
                if kind != "class":
                    line_caller = node_id
            else:
                truncated = True
        caller = line_caller or (scopes[-1][0] if scopes else None)
        for match in re.finditer(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", line):
            symbol = match.group(1)
            if symbol == definition_symbol or symbol.lower() in STOP_SYMBOLS:
                continue
            if len(calls) >= max_calls:
                truncated = True
                break
            calls.append(
                CallSite(
                    caller or module_node(),
                    symbol,
                    relative,
                    line_number,
                    _safe_evidence(line),
                    "regex",
                )
            )
        opens = line.count("{")
        closes = line.count("}")
        if line_caller and opens > closes:
            scopes.append((line_caller, brace_depth))
        brace_depth += opens - closes
    return list(nodes.values()), calls, truncated, False


def _impact_paths(
    nodes: list[GraphNode], edges: list[dict[str, Any]], max_depth: int = 4, max_paths: int = 200
) -> list[dict[str, Any]]:
    by_id = {node.id: node for node in nodes}
    reverse: dict[str, set[str]] = {}
    for edge in edges:
        for target in edge["resolved_callees"]:
            reverse.setdefault(target, set()).add(edge["caller"])
    results: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, ...]] = set()
    for changed in (node for node in nodes if node.changed):
        queue: list[tuple[str, list[str]]] = [(changed.id, [changed.id])]
        while queue and len(results) < max_paths:
            current, path = queue.pop(0)
            if len(path) > max_depth:
                continue
            for caller in sorted(reverse.get(current, set())):
                if caller in path:
                    continue
                expanded = [caller, *path]
                key = tuple(expanded)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                results.append(
                    {
                        "changed_symbol": changed.symbol,
                        "depth": len(expanded) - 1,
                        "path": [
                            {
                                "id": node_id,
                                "symbol": by_id[node_id].symbol,
                                "file": by_id[node_id].file,
                                "line": by_id[node_id].line,
                            }
                            for node_id in expanded
                            if node_id in by_id
                        ],
                    }
                )
                queue.append((caller, expanded))
    return results


def build_lightweight_call_graph(
    files: list[Path],
    root: Path,
    changed_objects: list[ChangedSymbol],
    *,
    max_nodes: int,
    max_edges: int,
    timeout: int,
) -> dict[str, Any]:
    changed_symbols = {
        item.symbol for item in changed_objects if IDENTIFIER_RE.match(item.symbol)
    }
    nodes: list[GraphNode] = []
    calls: list[CallSite] = []
    scanned = 0
    parse_failures: list[str] = []
    truncated = False
    deadline = time.monotonic() + timeout
    graph_files = [path for path in files if path.suffix.lower() in GRAPH_SUFFIXES]
    for path in graph_files:
        if time.monotonic() >= deadline or len(nodes) >= max_nodes or len(calls) >= max_edges:
            truncated = True
            break
        relative = path.relative_to(root).as_posix()
        remaining_nodes = max_nodes - len(nodes)
        remaining_calls = max_edges - len(calls)
        if path.suffix.lower() == ".py":
            parsed_nodes, parsed_calls, limited, parse_failed = _scan_python_graph(
                path, relative, changed_symbols, remaining_nodes, remaining_calls, deadline
            )
        else:
            parsed_nodes, parsed_calls, limited, parse_failed = _scan_generic_graph(
                path, relative, changed_symbols, remaining_nodes, remaining_calls, deadline
            )
        if parse_failed:
            parse_failures.append(relative)
        nodes.extend(parsed_nodes)
        calls.extend(parsed_calls)
        scanned += 1
        truncated = truncated or limited

    symbol_index: dict[str, list[str]] = {}
    for node in nodes:
        if node.kind != "module":
            symbol_index.setdefault(node.symbol, []).append(node.id)
    edges: list[dict[str, Any]] = []
    for call in calls[:max_edges]:
        resolved = symbol_index.get(call.callee_symbol, [])[:5]
        edges.append(
            {
                "caller": call.caller,
                "callee_symbol": call.callee_symbol,
                "resolved_callees": resolved,
                "file": call.file,
                "line": call.line,
                "evidence": call.evidence,
                "parser": call.parser,
                "confidence": "medium" if len(resolved) == 1 else "low",
            }
        )
    complete = not truncated and not parse_failures and scanned == len(graph_files)
    return {
        "repository_scan": {
            "candidate_source_files": len(graph_files),
            "source_files_scanned": scanned,
            "definitions_indexed": len(nodes),
            "call_sites_indexed": len(calls),
            "call_edges_emitted": len(edges),
            "scan_complete": complete,
            "parse_failures": parse_failures[:50],
        },
        "nodes": [node.__dict__ for node in nodes],
        "edges": edges,
        "impact_paths": _impact_paths(nodes, edges),
        "limits": [
            "Python relationships use AST; JavaScript, TypeScript, Java, and Vue use lightweight regex and brace scopes.",
            "Name resolution is local and heuristic; overloads, imports, inheritance, dynamic dispatch, reflection, and generated code may be ambiguous.",
        ],
    }


def collect_impact(
    repo: Path,
    revision_range: str,
    *,
    max_results: int = 500,
    max_per_object: int = 30,
    max_file_bytes: int = 1_000_000,
    max_files: int = 20_000,
    max_graph_nodes: int = 10_000,
    max_call_edges: int = 20_000,
    timeout: int = 60,
) -> dict[str, Any]:
    root = Path(run_git(repo, "rev-parse", "--show-toplevel", timeout=timeout).strip())
    changed_objects, callees = extract_changed_objects(root, revision_range, timeout)
    searchable_with_probe = list(iter_searchable_files(root, max_file_bytes, max_files + 1))
    file_limit_reached = len(searchable_with_probe) > max_files
    searchable = searchable_with_probe[:max_files]
    call_graph = build_lightweight_call_graph(
        searchable,
        root,
        changed_objects,
        max_nodes=max_graph_nodes,
        max_edges=max_call_edges,
        timeout=timeout,
    )
    call_graph["repository_scan"].update(
        {
            "searchable_files_considered": len(searchable),
            "searchable_file_limit_reached": file_limit_reached,
            "max_file_bytes": max_file_bytes,
            "excluded_directories": sorted(EXCLUDED_PARTS),
        }
    )
    if file_limit_reached:
        call_graph["repository_scan"]["scan_complete"] = False
    deadline = time.monotonic() + timeout
    symbol_references: list[dict[str, Any]] = []
    api_references: list[dict[str, Any]] = []
    unresolved: list[str] = []
    truncated = file_limit_reached
    search_timed_out = False

    targets = [(item.symbol, item.kind) for item in changed_objects]
    known = {symbol for symbol, _ in targets}
    targets.extend((callee, "callee") for callee in callees if callee not in known)
    for symbol, kind in targets:
        if len(symbol) < 3:
            continue
        pattern = re.compile(re.escape(symbol) if kind.startswith("api:") else rf"\b{re.escape(symbol)}\b")
        found = 0
        for path in searchable:
            if time.monotonic() >= deadline:
                truncated = True
                search_timed_out = True
                break
            if len(symbol_references) + len(api_references) >= max_results:
                truncated = True
                break
            relative = path.relative_to(root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, 1):
                if not pattern.search(line):
                    continue
                reference_type = "api_consumer" if kind.startswith("api:") else _reference_type(relative, line, symbol)
                item = _reference(symbol, relative, line_number, line, reference_type)
                (api_references if kind.startswith("api:") else symbol_references).append(item)
                found += 1
                if found >= max_per_object:
                    truncated = True
                    break
            if found >= max_per_object or truncated and len(symbol_references) + len(api_references) >= max_results:
                break
        if not found:
            unresolved.append(symbol)
        if search_timed_out:
            break

    all_references = symbol_references + api_references
    related_tests = [item for item in all_references if item["reference_type"] == "related_test"]
    frontend = [item for item in all_references if _has_marker(item["file"], item["evidence"], FRONTEND_MARKERS)]
    backend = [item for item in symbol_references if item["reference_type"] == "direct_caller" and _has_marker(item["file"], item["evidence"], BACKEND_MARKERS)]
    database = [item for item in all_references if _has_marker(item["file"], item["evidence"], DB_MARKERS)]
    configuration = [item for item in all_references if _has_marker(item["file"], item["evidence"], CONFIG_MARKERS)]
    scheduled = [item for item in all_references if _has_marker(item["file"], item["evidence"], SCHEDULE_MARKERS)]
    consumers = [item for item in all_references if _has_marker(item["file"], item["evidence"], MESSAGE_MARKERS)]
    limits = [
        "Regex and bounded text search do not form a complete semantic call graph.",
        "Dynamic dispatch, reflection, generated code, runtime configuration, and external repositories may be unresolved.",
        f"Excluded directories: {', '.join(sorted(EXCLUDED_PARTS))}.",
    ]
    if truncated:
        limits.append("Reference results were truncated by configured limits.")
    if file_limit_reached:
        limits.append(f"Repository search stopped after {max_files} searchable files.")
    if search_timed_out:
        limits.append(f"Repository reference search timed out after {timeout}s.")
    return {
        "repository": str(root),
        "range": revision_range,
        "analysis_mode": "Full repository context analysis",
        "changed_symbols": [item.__dict__ for item in changed_objects],
        "changed_callees": callees,
        "repository_scan": call_graph["repository_scan"],
        "lightweight_call_graph": {
            key: value for key, value in call_graph.items() if key != "repository_scan"
        },
        "symbol_references": symbol_references,
        "api_references": api_references,
        "frontend_consumers": frontend,
        "backend_callers": backend,
        "database_dependencies": database,
        "configuration_references": configuration,
        "scheduled_jobs": scheduled,
        "message_consumers": consumers,
        "related_tests": related_tests,
        "unresolved_symbols": sorted(set(unresolved)),
        "search_truncated": truncated,
        "analysis_limits": limits,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Complete repository path")
    parser.add_argument("--range", dest="revision_range", required=True, help="Git range BASE...HEAD")
    parser.add_argument("--max-results", type=int, default=500)
    parser.add_argument("--max-per-object", type=int, default=30)
    parser.add_argument("--max-file-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-files", type=int, default=20_000)
    parser.add_argument("--max-graph-nodes", type=int, default=10_000)
    parser.add_argument("--max-call-edges", type=int, default=20_000)
    parser.add_argument("--timeout", type=int, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = collect_impact(
            Path(args.repo).expanduser().resolve(),
            args.revision_range,
            max_results=max(args.max_results, 1),
            max_per_object=max(args.max_per_object, 1),
            max_file_bytes=max(args.max_file_bytes, 1),
            max_files=max(args.max_files, 1),
            max_graph_nodes=max(args.max_graph_nodes, 1),
            max_call_edges=max(args.max_call_edges, 1),
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
                "analysis_limits": ["未获取完整仓库，无法确认所有间接调用方和完整回归范围。"],
            },
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
