"""Normalize DingTalk/TAPD markdown so embedded HTML becomes readable text."""

from __future__ import annotations

import re
from html import unescape


_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>", re.I)
_TABLE_RE = re.compile(
    r"(?:^|\n)(\|[^\n]+\|\r?\n\|[ \t:|\-]+\|\r?\n(?:\|[^\n]*\|\r?\n?)*)",
    re.M,
)
_MARGIN_RE = re.compile(r"margin-left\s*:\s*(\d+(?:\.\d+)?)\s*(em|px)", re.I)
_LIST_LINE_RE = re.compile(r"^[ \t]*(?:\d+\.|-|\*)\s+")


def _margin_level(attrs: str) -> int:
    match = _MARGIN_RE.search(attrs or "")
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "em":
        return max(0, int(round(value)))
    return max(0, int(round(value / 16.0)))


def _li_open_to_prefix(attrs: str) -> str:
    """Turn <li ...> into a newline + indent. Item text usually already has 1. / 2."""
    level = _margin_level(attrs)
    return "\n" + ("  " * level)


def html_fragment_to_markdown(value: str) -> str:
    """Convert common HTML fragments inside exported cells to markdown text."""
    text = unescape(str(value or ""))
    if "<" not in text:
        return _compact_list_spacing(text.strip())

    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<p[^>]*>", "", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)<div[^>]*>", "", text)
    text = re.sub(r"(?i)</h([1-6])\s*>", "\n\n", text)
    text = re.sub(
        r"(?i)<h([1-6])[^>]*>",
        lambda match: "#" * int(match.group(1)) + " ",
        text,
    )
    text = re.sub(r"(?i)<li([^>]*)>", lambda match: _li_open_to_prefix(match.group(1)), text)
    text = re.sub(r"(?i)</li\s*>", "", text)
    text = re.sub(r"(?i)</?(ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"(?i)<strong[^>]*>", "**", text)
    text = re.sub(r"(?i)</strong\s*>", "**", text)
    text = re.sub(r"(?i)<b\b[^>]*>", "**", text)
    text = re.sub(r"(?i)</b\s*>", "**", text)
    text = re.sub(r"(?i)<em\b[^>]*>", "*", text)
    text = re.sub(r"(?i)</em\s*>", "*", text)
    text = re.sub(r"(?i)<i\b[^>]*>", "*", text)
    text = re.sub(r"(?i)</i\s*>", "*", text)
    text = re.sub(r"(?i)<s\b[^>]*>", "~~", text)
    text = re.sub(r"(?i)</s\s*>", "~~", text)
    text = re.sub(r"(?i)<del\b[^>]*>", "~~", text)
    text = re.sub(r"(?i)</del\s*>", "~~", text)
    text = re.sub(
        r'(?i)<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a\s*>',
        lambda match: f"[{_strip_tags(match.group(2)).strip() or match.group(1)}]({match.group(1)})",
        text,
        flags=re.S,
    )
    text = re.sub(
        r'(?i)<img[^>]*src=["\']([^"\']+)["\'][^>]*(?:alt=["\']([^"\']*)["\'])?[^>]*/?>',
        lambda match: f"![{match.group(2) or 'image'}]({match.group(1)})",
        text,
    )
    text = re.sub(
        r'(?i)<img[^>]*(?:alt=["\']([^"\']*)["\'])?[^>]*src=["\']([^"\']+)["\'][^>]*/?>',
        lambda match: f"![{match.group(1) or 'image'}]({match.group(2)})",
        text,
    )
    # Drop remaining tags but keep their text (e.g. styled spans).
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = text.replace(r"\+", "+")
    text = re.sub(r"[ \t]+\n", "\n", text)
    # Lines that lost markers but were list children: leave as indented text.
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    return _compact_list_spacing(text.strip())


def _compact_list_spacing(text: str) -> str:
    """Keep consecutive list items tight; preserve a single blank line between paragraphs."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").split("\n")
    compact: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if compact and compact[-1] != "":
                compact.append("")
            continue
        is_list = bool(_LIST_LINE_RE.match(line))
        if is_list and compact and compact[-1] == "":
            prev = compact[-2] if len(compact) >= 2 else ""
            if _LIST_LINE_RE.match(prev or ""):
                compact.pop()
        compact.append(line.rstrip())
    while compact and compact[-1] == "":
        compact.pop()
    return "\n".join(compact)


def _strip_tags(value: str) -> str:
    return _TAG_RE.sub("", value or "")


def _split_row(line: str) -> list[str]:
    raw = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "`":
            in_code = not in_code
            current.append(char)
            index += 1
            continue
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_separator(line: str) -> bool:
    cells = _split_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _escape_cell(text: str) -> str:
    """Keep table grid: put multi-line cell content on one row with <br>."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    rendered: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        # Preserve leading indent inside table cells for nested lists.
        leading = len(line) - len(line.lstrip(" "))
        indent = "&nbsp;" * leading
        rendered.append(indent + line.lstrip(" ").replace("|", r"\|"))
    return "<br>".join(rendered)


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    """Always keep Markdown table structure; only sanitize cell HTML."""
    width = max((len(header), *(len(row) for row in rows)), default=0)
    labels = (header + [""] * width)[:width]
    cleaned_rows = [
        [_escape_cell(html_fragment_to_markdown(cell)) for cell in (row + [""] * width)[:width]]
        for row in rows
    ]
    header_cells = [_escape_cell(html_fragment_to_markdown(cell)) or " " for cell in labels]
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join("---" for _ in header_cells) + " |",
    ]
    for row in cleaned_rows:
        lines.append("| " + " | ".join(cell or " " for cell in row) + " |")
    return "\n".join(lines)


def _normalize_table_block(block: str) -> str:
    lines = [line for line in block.strip("\n").splitlines() if line.strip()]
    if len(lines) < 2 or not _is_separator(lines[1]):
        return html_fragment_to_markdown(block) if "<" in block else block
    header = _split_row(lines[0])
    rows = [_split_row(line) for line in lines[2:]]
    return _render_table(header, rows)


def normalize_requirement_markdown(markdown: str) -> str:
    """
    Clean requirement markdown from DingTalk/TAPD exporters.

    - Convert HTML fragments inside cells to readable text
    - Keep Markdown table grids (use <br> for multi-line cells)
    - Preserve nested list indentation from margin-left
    """
    text = str(markdown or "")
    if not text.strip():
        return ""

    parts: list[str] = []
    cursor = 0
    for match in _TABLE_RE.finditer(text):
        start, end = match.span(1)
        prefix = text[cursor:start]
        if prefix:
            parts.append(html_fragment_to_markdown(prefix) if "<" in prefix else prefix.rstrip("\n"))
        parts.append(_normalize_table_block(match.group(1)))
        cursor = end
    suffix = text[cursor:]
    if suffix:
        parts.append(html_fragment_to_markdown(suffix) if "<" in suffix else suffix)

    merged = "\n\n".join(part.strip("\n") for part in parts if part and part.strip())
    merged = _compact_list_spacing(merged)
    merged = re.sub(r"\n{3,}", "\n\n", merged).strip()
    return merged + ("\n" if merged else "")
