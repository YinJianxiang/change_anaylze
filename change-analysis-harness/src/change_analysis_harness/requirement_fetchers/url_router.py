"""Classify and parse requirement document URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

UrlKind = Literal["tapd_story", "dingtalk_doc", "unsupported"]


@dataclass(frozen=True)
class ParsedUrl:
    kind: UrlKind
    url: str
    workspace_id: str = ""
    story_id: str = ""
    node_id: str = ""


_DINGTALK_NODE_RE = re.compile(
    r"(?:alidocs\.dingtalk\.com|docs\.dingtalk\.com)/i/nodes/([A-Za-z0-9_-]+)",
    re.I,
)
_TAPD_HOST_RE = re.compile(r"(?:^|\.)tapd\.cn$", re.I)
_TAPD_WS_RE = re.compile(r"tapd\.cn/(?:tapd_fe/)?(\d+)/", re.I)
_TAPD_STORY_VIEW_RE = re.compile(r"/stories/view/(\d+)", re.I)
_TAPD_STORY_DETAIL_RE = re.compile(r"/story/detail/(\d+)", re.I)
_TAPD_DIALOG_STORY_RE = re.compile(r"dialog_preview_id=story_(\d+)", re.I)
_TAPD_STORY_ID_QUERY_RE = re.compile(r"(?:^|[?&])story_id=(\d+)", re.I)


def parse_requirement_url(url: str) -> ParsedUrl:
    text = str(url or "").strip()
    if not text:
        return ParsedUrl(kind="unsupported", url=text)

    dingtalk = parse_dingtalk_node_id(text)
    if dingtalk:
        return ParsedUrl(kind="dingtalk_doc", url=text, node_id=dingtalk)

    if _is_tapd_url(text):
        workspace_id, story_id = parse_tapd_story_ref(text)
        if story_id:
            return ParsedUrl(
                kind="tapd_story",
                url=text,
                workspace_id=workspace_id,
                story_id=story_id,
            )

    return ParsedUrl(kind="unsupported", url=text)


def parse_dingtalk_node_id(text: str) -> str:
    match = _DINGTALK_NODE_RE.search(text or "")
    if match:
        return match.group(1)
    # bare dentryUuid (32+ alnum), used by DingTalk MCP
    bare = str(text or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", bare) and "://" not in bare:
        return bare
    return ""


def parse_tapd_story_ref(text: str) -> tuple[str, str]:
    workspace_id = ""
    story_id = ""
    ws_match = _TAPD_WS_RE.search(text)
    if ws_match:
        workspace_id = ws_match.group(1)

    for pattern in (
        _TAPD_DIALOG_STORY_RE,
        _TAPD_STORY_VIEW_RE,
        _TAPD_STORY_DETAIL_RE,
        _TAPD_STORY_ID_QUERY_RE,
    ):
        match = pattern.search(text)
        if match:
            story_id = match.group(1)
            break

    if not story_id:
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        for key in ("story_id", "id"):
            values = query.get(key) or []
            if values and str(values[0]).isdigit():
                story_id = str(values[0])
                break
        dialog = (query.get("dialog_preview_id") or [""])[0]
        dialog_match = re.match(r"story_(\d+)$", str(dialog), re.I)
        if dialog_match:
            story_id = dialog_match.group(1)

    return workspace_id, story_id


def extract_dingtalk_urls(text: str) -> list[str]:
    """Collect unique DingTalk doc URLs from HTML/markdown/plain text."""
    blob = str(text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"https?://(?:alidocs|docs)\.dingtalk\.com/i/nodes/[A-Za-z0-9_-]+[^\s\"'<>)]*",
        blob,
        re.I,
    ):
        url = match.group(0).rstrip(".,;，。；)")
        node_id = parse_dingtalk_node_id(url)
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        found.append(url)
    return found


def _is_tapd_url(text: str) -> bool:
    lowered = text.lower()
    if "tapd.cn" in lowered:
        return True
    try:
        host = urlparse(text).hostname or ""
    except ValueError:
        return False
    return bool(_TAPD_HOST_RE.search(host))
