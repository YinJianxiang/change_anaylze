"""Fetch DingTalk online-doc markdown (images kept as markdown links)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from orchestrator.services.requirement_fetchers.markdown_normalize import (
    normalize_requirement_markdown,
)
from orchestrator.services.requirement_fetchers.url_router import parse_dingtalk_node_id

_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}


class DingTalkFetchError(RuntimeError):
    """Raised when DingTalk document fetch fails."""


def _timeout_sec() -> int:
    try:
        return max(int(os.environ.get("REQUIREMENT_FETCH_TIMEOUT_SEC", "30")), 5)
    except ValueError:
        return 30


def _urlopen_json(
    req: urllib.request.Request,
    timeout: int,
) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise DingTalkFetchError(f"DingTalk HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise DingTalkFetchError(f"DingTalk network error: {exc}") from exc
    if not isinstance(payload, dict):
        raise DingTalkFetchError("DingTalk response is not a JSON object")
    return payload


def _get_access_token() -> str:
    explicit = os.environ.get("DINGTALK_ACCESS_TOKEN", "").strip()
    if explicit:
        return explicit

    app_key = os.environ.get("DINGTALK_APP_KEY", "").strip()
    app_secret = os.environ.get("DINGTALK_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise DingTalkFetchError(
            "DingTalk credentials missing: set DINGTALK_DOC_BRIDGE_URL, "
            "DINGTALK_ACCESS_TOKEN, or DINGTALK_APP_KEY + DINGTALK_APP_SECRET"
        )

    now = time.time()
    if _TOKEN_CACHE["token"] and float(_TOKEN_CACHE["expires_at"]) > now + 60:
        return str(_TOKEN_CACHE["token"])

    body = json.dumps({"appKey": app_key, "appSecret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.dingtalk.com/v1.0/oauth2/accessToken",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = _urlopen_json(req, _timeout_sec())
    token = str(payload.get("accessToken") or "").strip()
    if not token:
        raise DingTalkFetchError(f"DingTalk accessToken missing: {payload}")
    expire_in = int(payload.get("expireIn") or 7200)
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + expire_in
    return token


def _operator_id() -> str:
    value = os.environ.get("DINGTALK_OPERATOR_ID", "").strip()
    if not value:
        raise DingTalkFetchError("DINGTALK_OPERATOR_ID is required for OpenAPI doc fetch")
    return value


def _fetch_via_bridge(node_id: str) -> dict[str, Any]:
    bridge = os.environ.get("DINGTALK_DOC_BRIDGE_URL", "").strip()
    if not bridge:
        raise DingTalkFetchError("DINGTALK_DOC_BRIDGE_URL not set")

    body = json.dumps({"nodeId": node_id, "format": "markdown"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    bridge_token = os.environ.get("DINGTALK_DOC_BRIDGE_TOKEN", "").strip()
    if bridge_token:
        headers["Authorization"] = f"Bearer {bridge_token}"
    req = urllib.request.Request(bridge, data=body, headers=headers, method="POST")
    payload = _urlopen_json(req, _timeout_sec())
    markdown = ""
    for key in ("markdown", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            markdown = value.strip()
            break
    data = payload.get("data")
    if not markdown and isinstance(data, dict):
        for key in ("markdown", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                markdown = value.strip()
                break
    if not markdown and isinstance(data, str) and data.strip():
        markdown = data.strip()
    if not markdown:
        raise DingTalkFetchError(f"DingTalk bridge returned empty markdown: {payload}")
    title = str(payload.get("title") or node_id).strip()
    return {
        "title": title,
        "content": normalize_requirement_markdown(markdown),
        "node_id": node_id,
        "source": "bridge",
    }


def _block_text(block: dict[str, Any]) -> str:
    for key in ("heading", "paragraph", "orderedList", "unorderedList"):
        node = block.get(key)
        if isinstance(node, dict) and "text" in node:
            return str(node.get("text") or "")
    return ""


def _render_table(table: dict[str, Any]) -> str:
    cells = table.get("cells")
    if not isinstance(cells, list) or not cells:
        return ""
    rows: list[list[str]] = []
    for row in cells:
        if isinstance(row, list):
            rows.append([str(cell or "").replace("\n", "<br>") for cell in row])
        else:
            rows.append([str(row)])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_blocks(blocks: list[Any]) -> str:
    parts: list[str] = []
    for item in blocks:
        element = item.get("element") if isinstance(item, dict) and "element" in item else item
        if not isinstance(element, dict):
            continue
        block_type = str(element.get("blockType") or item.get("blockType") or "").strip()
        if block_type == "heading":
            heading = element.get("heading") or {}
            level_name = str(heading.get("level") or "heading-3")
            level = 3
            match = re_level(level_name)
            if match:
                level = match
            text = str(heading.get("text") or "").strip()
            if text:
                parts.append(f"{'#' * level} {text}")
        elif block_type == "paragraph":
            text = _block_text(element).strip()
            if text:
                parts.append(text)
        elif block_type == "orderedList":
            text = _block_text(element).strip()
            if text:
                parts.append(f"1. {text}")
        elif block_type in {"unorderedList", "bulletList"}:
            text = _block_text(element).strip()
            if text:
                parts.append(f"- {text}")
        elif block_type == "table":
            table = element.get("table") if isinstance(element.get("table"), dict) else element
            rendered = _render_table(table if isinstance(table, dict) else {})
            if rendered:
                parts.append(rendered)
        elif block_type == "image":
            image = element.get("image") if isinstance(element.get("image"), dict) else {}
            src = str(image.get("url") or image.get("src") or "").strip()
            alt = str(image.get("name") or image.get("alt") or "image").strip()
            if src:
                parts.append(f"![{alt}]({src})")
        else:
            text = _block_text(element).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def re_level(level_name: str) -> int:
    mapping = {
        "heading-1": 1,
        "heading-2": 2,
        "heading-3": 3,
        "heading-4": 4,
        "heading-5": 5,
        "heading-6": 6,
    }
    return mapping.get(level_name, 3)


def _fetch_via_openapi(node_id: str) -> dict[str, Any]:
    token = _get_access_token()
    operator_id = _operator_id()
    headers = {
        "Content-Type": "application/json",
        "x-acs-dingtalk-access-token": token,
    }

    title = node_id
    try:
        info_url = (
            f"https://api.dingtalk.com/v2.0/wiki/nodes/{urllib.parse.quote(node_id)}"
            f"?operatorId={urllib.parse.quote(operator_id)}"
        )
        info_req = urllib.request.Request(info_url, headers=headers, method="GET")
        info = _urlopen_json(info_req, _timeout_sec())
        node = info.get("node") if isinstance(info.get("node"), dict) else info
        if isinstance(node, dict) and node.get("name"):
            title = str(node["name"]).strip() or title
    except DingTalkFetchError:
        pass

    blocks_url = (
        f"https://api.dingtalk.com/v1.0/doc/suites/documents/{urllib.parse.quote(node_id)}/blocks"
        f"?operatorId={urllib.parse.quote(operator_id)}"
    )
    blocks_req = urllib.request.Request(blocks_url, headers=headers, method="GET")
    payload = _urlopen_json(blocks_req, _timeout_sec())
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    data = result.get("data") if isinstance(result, dict) else None
    if data is None and isinstance(payload.get("data"), list):
        data = payload["data"]
    if not isinstance(data, list):
        raise DingTalkFetchError(f"DingTalk blocks payload unexpected: {payload}")

    markdown = _render_blocks(data)
    if not markdown:
        raise DingTalkFetchError("DingTalk document blocks rendered empty markdown")
    return {
        "title": title,
        "content": normalize_requirement_markdown(markdown),
        "node_id": node_id,
        "source": "openapi_blocks",
    }


def fetch_document(url_or_node_id: str) -> dict[str, Any]:
    node_id = parse_dingtalk_node_id(url_or_node_id)
    if not node_id:
        raise DingTalkFetchError(f"Cannot parse DingTalk nodeId from: {url_or_node_id!r}")

    bridge = os.environ.get("DINGTALK_DOC_BRIDGE_URL", "").strip()
    if bridge:
        return _fetch_via_bridge(node_id)

    has_token = bool(os.environ.get("DINGTALK_ACCESS_TOKEN", "").strip())
    has_app = bool(
        os.environ.get("DINGTALK_APP_KEY", "").strip()
        and os.environ.get("DINGTALK_APP_SECRET", "").strip()
    )
    if not has_token and not has_app:
        raise DingTalkFetchError(
            "DingTalk credentials missing: set DINGTALK_DOC_BRIDGE_URL, "
            "DINGTALK_ACCESS_TOKEN, or DINGTALK_APP_KEY + DINGTALK_APP_SECRET "
            "(plus DINGTALK_OPERATOR_ID for OpenAPI)"
        )
    return _fetch_via_openapi(node_id)
