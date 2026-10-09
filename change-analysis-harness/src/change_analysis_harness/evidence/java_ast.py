from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any, Iterable


CLASS_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "annotation_type_declaration": "annotation",
    "record_declaration": "record",
}
CALLABLE_TYPES = {
    "method_declaration": "method",
    "constructor_declaration": "constructor",
}


class JavaAstUnavailable(RuntimeError):
    """Raised when tree-sitter Java support is not installed."""


@dataclass(frozen=True)
class JavaScope:
    id: str
    symbol: str
    qualified_name: str
    kind: str
    owner: str | None
    start_line: int
    end_line: int
    changed_lines: list[int]
    signature: str
    annotations: list[str]
    revision: str
    parser: str = "tree-sitter-java"


@dataclass(frozen=True)
class JavaCall:
    caller: str
    callee_symbol: str
    receiver: str | None
    line: int
    evidence: str
    call_type: str
    revision: str
    parser: str = "tree-sitter-java"


SUPPORTED_TREE_SITTER = "0.23.2"
SUPPORTED_TREE_SITTER_JAVA = "0.23.5"


def _installed_version(distribution: str) -> str | None:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return None


def _parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java
    except ImportError as exc:
        raise JavaAstUnavailable(
            "Java AST ?? tree-sitter ? tree-sitter-java????? Harness ???"
        ) from exc

    installed_tree_sitter = _installed_version("tree-sitter")
    installed_tree_sitter_java = _installed_version("tree-sitter-java")
    expected = (
        f"tree-sitter=={SUPPORTED_TREE_SITTER} ? "
        f"tree-sitter-java=={SUPPORTED_TREE_SITTER_JAVA}"
    )
    if (
        installed_tree_sitter != SUPPORTED_TREE_SITTER
        or installed_tree_sitter_java != SUPPORTED_TREE_SITTER_JAVA
    ):
        raise JavaAstUnavailable(
            "?? Java AST ?????????"
            f"tree-sitter={installed_tree_sitter or '<???>'}, "
            f"tree-sitter-java={installed_tree_sitter_java or '<???>'}?"
            f"??? {expected}?"
        )

    return Parser(Language(tree_sitter_java.language()))


def _node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _compact(value: str, limit: int = 260) -> str:
    compact = " ".join(value.strip().split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _named_child(node: Any, field: str) -> Any | None:
    try:
        return node.child_by_field_name(field)
    except (AttributeError, TypeError):
        return None


def _name(source: bytes, node: Any) -> str:
    child = _named_child(node, "name")
    if child is not None:
        return _node_text(source, child)
    for candidate in node.named_children:
        if candidate.type in {"identifier", "type_identifier"}:
            return _node_text(source, candidate)
    return "<anonymous>"


def _annotations(source: bytes, node: Any) -> list[str]:
    result: list[str] = []
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    if modifiers is None:
        return result
    for child in modifiers.named_children:
        if child.type in {"annotation", "marker_annotation"}:
            result.append(_compact(_node_text(source, child), 160))
    return result


def _signature(source: bytes, node: Any) -> str:
    body = _named_child(node, "body")
    end = body.start_byte if body is not None else node.end_byte
    return _compact(source[node.start_byte:end].decode("utf-8", errors="replace"), 500)


def _intersecting_lines(node: Any, changed_lines: set[int]) -> list[int]:
    start = node.start_point.row + 1
    end = node.end_point.row + 1
    return sorted(line for line in changed_lines if start <= line <= end)


def _line_evidence(source_lines: list[str], line: int) -> str:
    if 0 < line <= len(source_lines):
        return _compact(source_lines[line - 1], 260)
    return ""


def _walk(node: Any) -> Iterable[Any]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _calls_in_scope(source: bytes, scope_node: Any, scope_id: str, revision: str) -> list[JavaCall]:
    source_lines = source.decode("utf-8", errors="replace").splitlines()
    calls: list[JavaCall] = []

    def visit(node: Any, is_root: bool = False) -> None:
        if not is_root and node.type in CALLABLE_TYPES:
            # A nested/anonymous callable owns its own calls; do not attribute them to the outer method.
            return
        if node.type == "method_invocation":
            name_node = _named_child(node, "name")
            if name_node is None:
                return
            symbol = _node_text(source, name_node)
            object_node = _named_child(node, "object")
            receiver = _compact(_node_text(source, object_node), 120) if object_node is not None else None
            line = node.start_point.row + 1
            calls.append(
                JavaCall(
                    caller=scope_id,
                    callee_symbol=symbol,
                    receiver=receiver,
                    line=line,
                    evidence=_line_evidence(source_lines, line),
                    call_type="method_invocation",
                    revision=revision,
                )
            )
        elif node.type == "object_creation_expression":
            type_node = _named_child(node, "type")
            if type_node is None:
                return
            symbol = _compact(_node_text(source, type_node), 120)
            line = node.start_point.row + 1
            calls.append(
                JavaCall(
                    caller=scope_id,
                    callee_symbol=symbol,
                    receiver=None,
                    line=line,
                    evidence=_line_evidence(source_lines, line),
                    call_type="constructor_call",
                    revision=revision,
                )
            )
        elif node.type == "method_reference":
            name_node = _named_child(node, "name")
            if name_node is None:
                named = node.named_children
                name_node = named[-1] if named else None
            if name_node is None:
                return
            symbol = _compact(_node_text(source, name_node), 120)
            line = node.start_point.row + 1
            calls.append(
                JavaCall(
                    caller=scope_id,
                    callee_symbol=symbol,
                    receiver=None,
                    line=line,
                    evidence=_line_evidence(source_lines, line),
                    call_type="method_reference",
                    revision=revision,
                )
            )
        for child in node.named_children:
            visit(child)

    visit(scope_node, True)
    unique = {(item.caller, item.callee_symbol, item.line, item.call_type): item for item in calls}
    return sorted(unique.values(), key=lambda item: (item.line, item.callee_symbol, item.call_type))


def parse_changed_java_source(
    *,
    path: str,
    source_text: str,
    changed_lines: set[int],
    revision: str,
) -> dict[str, Any]:
    """Parse one Java revision and keep only AST scopes touched by changed lines."""
    if not changed_lines:
        return {"scopes": [], "calls": [], "parse_errors": []}
    parser = _parser()
    source = source_text.encode("utf-8")
    tree = parser.parse(source)
    parse_errors: list[str] = []
    if tree.root_node.has_error:
        parse_errors.append(f"{path}@{revision}: Java AST 包含语法错误节点")

    scopes: list[JavaScope] = []
    calls: list[JavaCall] = []

    def visit(node: Any, owners: list[str]) -> None:
        node_kind = CLASS_TYPES.get(node.type) or CALLABLE_TYPES.get(node.type)
        next_owners = owners
        if node_kind:
            symbol = _name(source, node)
            owner = ".".join(owners) or None
            qualified_name = ".".join([*owners, symbol])
            touched = _intersecting_lines(node, changed_lines)
            scope_id = f"{path}::{qualified_name}@{node.start_point.row + 1}"
            if touched:
                scope = JavaScope(
                    id=scope_id,
                    symbol=symbol,
                    qualified_name=qualified_name,
                    kind=node_kind,
                    owner=owner,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    changed_lines=touched,
                    signature=_signature(source, node),
                    annotations=_annotations(source, node),
                    revision=revision,
                )
                scopes.append(scope)
                if node.type in CALLABLE_TYPES:
                    calls.extend(_calls_in_scope(source, node, scope_id, revision))
            if node.type in CLASS_TYPES:
                next_owners = [*owners, symbol]

        for child in node.named_children:
            visit(child, next_owners)

    visit(tree.root_node, [])
    unique_scopes = {(item.qualified_name, item.kind, item.start_line, item.revision): item for item in scopes}
    unique_calls = {(item.caller, item.callee_symbol, item.line, item.call_type, item.revision): item for item in calls}
    return {
        "scopes": [asdict(item) for item in sorted(unique_scopes.values(), key=lambda item: (item.start_line, item.kind))],
        "calls": [asdict(item) for item in sorted(unique_calls.values(), key=lambda item: (item.line, item.callee_symbol))],
        "parse_errors": parse_errors,
    }


def merge_java_revisions(
    *,
    path: str,
    old_result: dict[str, Any],
    new_result: dict[str, Any],
) -> dict[str, Any]:
    """Merge base/head AST scopes and classify them as added, modified, or deleted."""
    old_scopes = old_result.get("scopes", [])
    new_scopes = new_result.get("scopes", [])

    def key(item: dict[str, Any]) -> tuple[str, str]:
        # Qualified name plus kind is stable enough for change slicing. The signature is retained as evidence.
        return str(item.get("qualified_name", "")), str(item.get("kind", ""))

    old_by_key = {key(item): item for item in old_scopes}
    new_by_key = {key(item): item for item in new_scopes}
    merged: list[dict[str, Any]] = []
    for item_key in sorted(set(old_by_key) | set(new_by_key)):
        old = old_by_key.get(item_key)
        new = new_by_key.get(item_key)
        current = dict(new or old or {})
        current["change_type"] = "modified" if old and new else "added" if new else "deleted"
        current["old_scope"] = old
        current["new_scope"] = new
        merged.append(current)

    calls = [*old_result.get("calls", []), *new_result.get("calls", [])]
    unique_calls = {
        (item.get("caller"), item.get("callee_symbol"), item.get("line"), item.get("revision"), item.get("call_type")): item
        for item in calls
    }
    return {
        "path": path,
        "parser": "tree-sitter-java",
        "analysis_scope": "changed_lines_and_enclosing_ast_scopes_only",
        "changed_scopes": merged,
        "calls_from_changed_scopes": list(unique_calls.values()),
        "parse_errors": [*old_result.get("parse_errors", []), *new_result.get("parse_errors", [])],
    }

