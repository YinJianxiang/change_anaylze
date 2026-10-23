"""End-to-end requirement URL fetch pipeline."""

from __future__ import annotations

import os
import re
from html import unescape
from typing import Any

from change_analysis_harness.requirement_fetchers.dingtalk_fetcher import (
    DingTalkFetchError,
    fetch_document as fetch_dingtalk_document,
)
from change_analysis_harness.requirement_fetchers.markdown_normalize import (
    normalize_requirement_markdown,
)
from change_analysis_harness.requirement_fetchers.tapd_fetcher import (
    TapdFetchError,
    fetch_story as fetch_tapd_story,
)
from change_analysis_harness.requirement_fetchers.url_router import (
    extract_dingtalk_urls,
    parse_requirement_url,
)


def _max_chars() -> int:
    try:
        return max(int(os.environ.get("REQUIREMENT_FETCH_MAX_CHARS", "80000")), 1000)
    except ValueError:
        return 80000


def _truncate(content: str) -> tuple[str, bool]:
    limit = _max_chars()
    text = normalize_requirement_markdown(str(content or ""))
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n…[truncated by REQUIREMENT_FETCH_MAX_CHARS]", True


def _tapd_has_body(description: str) -> bool:
    """Ignore DingTalk links and their labels, but retain actual TAPD content."""
    # Image/media requirements are content even when wrapped in a document link.
    if re.search(r"<(?:img|video|audio|iframe)\b|!\[", description, re.I):
        return True
    text = normalize_requirement_markdown(description)
    text = re.sub(
        r'(?<!!)\[.*?\]\(<?(https?://[^\s)>]+)>?(?:\s+"[^"]*")?\)',
        lambda match: "" if extract_dingtalk_urls(match.group(1)) else match.group(0),
        text,
        flags=re.S,
    )
    text = re.sub(
        r"https?://[^\s<>]+",
        lambda match: "" if extract_dingtalk_urls(match.group(0)) else match.group(0),
        text,
    )
    # Empty list items and formatting around links are not requirement content.
    text = re.sub(r"(?m)^\s*\d+[.)]\s*", "", text)
    return bool(re.sub(r"[\s\u200b\ufeff#*_~`>+\-:：,，.。;；()（）\[\]<>]+", "", text))


def fetch_requirement_document(url: str) -> dict[str, Any]:
    """
    Fetch one requirement URL.

    Returns a document dict:
      url, status (FETCHED|PARTIAL|FAILED|UNSUPPORTED), content, meta
    """
    parsed = parse_requirement_url(url)
    meta: dict[str, Any] = {"kind": parsed.kind}

    if parsed.kind == "unsupported":
        return {
            "url": url,
            "status": "UNSUPPORTED",
            "content": None,
            "meta": {**meta, "error": "unsupported requirement URL"},
        }

    try:
        if parsed.kind == "dingtalk_doc":
            doc = fetch_dingtalk_document(parsed.url or parsed.node_id)
            content, truncated = _truncate(str(doc.get("content") or ""))
            return {
                "url": url,
                "status": "FETCHED",
                "content": content,
                "meta": {
                    **meta,
                    "title": doc.get("title"),
                    "node_id": doc.get("node_id") or parsed.node_id,
                    "source": doc.get("source"),
                    "truncated": truncated,
                },
            }

        # tapd_story
        story = fetch_tapd_story(parsed.workspace_id, parsed.story_id)
        meta.update(
            {
                "title": story.get("title"),
                "workspace_id": story.get("workspace_id"),
                "story_id": story.get("story_id"),
                "source": "tapd",
            }
        )
        # Use the raw description, before fetch_story adds the title/placeholder.
        description = unescape(str(story.get("description_html") or ""))
        if not description:
            raw_story = story.get("story")
            if isinstance(raw_story, dict):
                description = unescape(str(raw_story.get("description") or ""))
        nested_urls = extract_dingtalk_urls(description)
        # dedupe preserving order
        seen: set[str] = set()
        unique_nested: list[str] = []
        for item in nested_urls:
            node = parse_requirement_url(item).node_id or item
            if node in seen:
                continue
            seen.add(node)
            unique_nested.append(item)

        if not unique_nested or _tapd_has_body(description):
            content, truncated = _truncate(str(story.get("content") or ""))
            return {
                "url": url,
                "status": "FETCHED",
                "content": content,
                "meta": {**meta, "truncated": truncated, "nested_urls": unique_nested},
            }

        nested_parts: list[str] = []
        nested_errors: list[str] = []
        nested_meta: list[dict[str, Any]] = []
        for nested_url in unique_nested:
            try:
                nested = fetch_dingtalk_document(nested_url)
                nested_body = str(nested.get("content") or "").strip()
                if not nested_body:
                    raise DingTalkFetchError("DingTalk document returned empty content")
                nested_title = str(nested.get("title") or nested_url).strip()
                nested_parts.append(f"## 钉钉文档：{nested_title}\n\n来源：{nested_url}\n\n{nested_body}")
                nested_meta.append(
                    {
                        "url": nested_url,
                        "title": nested_title,
                        "node_id": nested.get("node_id"),
                        "source": nested.get("source"),
                        "status": "FETCHED",
                    }
                )
            except DingTalkFetchError as exc:
                nested_errors.append(f"{nested_url}: {exc}")
                nested_meta.append({"url": nested_url, "status": "FAILED", "error": str(exc)})

        content, truncated = _truncate(
            "# \u5173\u8054\u9489\u9489\u9700\u6c42\u6587\u6863\n\n" + "\n\n---\n\n".join(nested_parts)
            if nested_parts else ""
        )
        status = "PARTIAL" if nested_errors else "FETCHED"
        if not nested_parts:
            status = "FAILED"

        return {
            "url": url,
            "status": status,
            "content": content or None,
            "meta": {
                **meta,
                "source": "dingtalk",
                "truncated": truncated,
                "nested_urls": unique_nested,
                "nested": nested_meta,
                "error": "; ".join(nested_errors) if nested_errors else None,
            },
        }
    except (TapdFetchError, DingTalkFetchError) as exc:
        return {
            "url": url,
            "status": "FAILED",
            "content": None,
            "meta": {**meta, "error": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001 - isolate per-URL failures
        return {
            "url": url,
            "status": "FAILED",
            "content": None,
            "meta": {**meta, "error": f"unexpected: {exc}"},
        }
