"""Minimal TAPD OpenAPI client for story body fetch."""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from html import unescape
from typing import Any, Optional, Tuple

DEFAULT_TAPD_API_BASE_URL = "https://api.tapd.cn"
_DEFAULT_HTTP_RETRIES = 3
_DEFAULT_RETRY_BACKOFF_SEC = 0.8


class TapdFetchError(RuntimeError):
    """Raised when TAPD story fetch fails."""


def _timeout_sec() -> int:
    try:
        return max(int(os.environ.get("REQUIREMENT_FETCH_TIMEOUT_SEC", "30")), 5)
    except ValueError:
        return 30


def _get_base_url() -> str:
    return os.environ.get("TAPD_API_BASE_URL", DEFAULT_TAPD_API_BASE_URL).rstrip("/")


def _is_cloud() -> bool:
    return "api.tapd.cn" in _get_base_url()


def to_long_id(short_id: str, workspace_id: int) -> str:
    value = str(short_id).strip()
    if not value.isdigit() or len(value) > 9:
        return value
    prefix = "11" if _is_cloud() else "10"
    return f"{prefix}{workspace_id}{value.zfill(9)}"


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("TAPD_ACCESS_TOKEN", "").strip()
    if token:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Via": "mcp",
        }
    user = os.environ.get("TAPD_API_USER", "").strip()
    password = os.environ.get("TAPD_API_PASSWORD", "").strip()
    if not user or not password:
        raise TapdFetchError(
            "TAPD credentials missing: set TAPD_ACCESS_TOKEN or TAPD_API_USER + TAPD_API_PASSWORD"
        )
    auth = b64encode(f"{user}:{password}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "Via": "mcp",
    }


def _http_retry_config() -> Tuple[int, float]:
    try:
        retries = int(os.environ.get("TAPD_HTTP_RETRIES", _DEFAULT_HTTP_RETRIES))
    except ValueError:
        retries = _DEFAULT_HTTP_RETRIES
    try:
        backoff = float(os.environ.get("TAPD_HTTP_RETRY_BACKOFF", _DEFAULT_RETRY_BACKOFF_SEC))
    except ValueError:
        backoff = _DEFAULT_RETRY_BACKOFF_SEC
    return max(1, retries), max(0.0, backoff)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError, ssl.SSLError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _is_transient(reason)
        text = str(reason).lower()
        return any(token in text for token in ("timed out", "timeout", "reset", "eof", "ssl"))
    return False


def _urlopen_json(req: urllib.request.Request, timeout: int) -> dict[str, Any]:
    retries, backoff = _http_retry_config()
    last: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise TapdFetchError("TAPD response is not a JSON object")
            return payload
        except Exception as exc:  # noqa: BLE001 - retry wrapper
            last = exc
            if attempt >= retries or not _is_transient(exc):
                raise
            time.sleep(backoff * (2 ** (attempt - 1)))
    assert last is not None
    raise last


def request(method: str, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    base = _get_base_url()
    url = f"{base}/{endpoint.lstrip('/')}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}s=mcp"
    if params:
        url = f"{url}&{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers=_auth_headers(), method=method.upper())
    return _urlopen_json(req, _timeout_sec())


def html_description_to_markdown(html: str) -> str:
    """Best-effort HTML -> markdown; keep <img> as markdown images in place."""
    text = str(html or "")
    if not text.strip():
        return ""

    def replace_img(match: re.Match[str]) -> str:
        src = match.group(1) or ""
        alt = match.group(2) or "image"
        if src.startswith("/"):
            src = f"https://www.tapd.cn{src}"
        return f"![{alt}]({src})"

    text = re.sub(
        r'<img[^>]*src=["\']([^"\']+)["\'][^>]*(?:alt=["\']([^"\']*)["\'])?[^>]*/?>',
        replace_img,
        text,
        flags=re.I,
    )
    text = re.sub(
        r'<img[^>]*(?:alt=["\']([^"\']*)["\'])?[^>]*src=["\']([^"\']+)["\'][^>]*/?>',
        lambda m: f"![{m.group(1) or 'image'}]({('https://www.tapd.cn' + m.group(2)) if m.group(2).startswith('/') else m.group(2)})",
        text,
        flags=re.I,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"</(div|tr|li)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_story(workspace_id: str, story_id: str) -> dict[str, Any]:
    if not workspace_id or not str(workspace_id).isdigit():
        raise TapdFetchError(f"TAPD workspace_id missing or invalid: {workspace_id!r}")
    if not story_id or not str(story_id).isdigit():
        raise TapdFetchError(f"TAPD story_id missing or invalid: {story_id!r}")

    ws = int(workspace_id)
    long_id = to_long_id(story_id, ws)
    payload = request(
        "GET",
        "stories",
        params={
            "workspace_id": ws,
            "entity_type": "stories",
            "id": long_id,
            "fields": "id,name,description,status,owner,creator,modified",
            "limit": 1,
            "page": 1,
        },
    )
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise TapdFetchError(f"TAPD story not found: workspace={ws} id={long_id}")
    first = data[0]
    story = first.get("Story") if isinstance(first, dict) else None
    if not isinstance(story, dict):
        raise TapdFetchError("TAPD response missing Story object")

    name = str(story.get("name") or "").strip()
    description_html = str(story.get("description") or "")
    markdown = html_description_to_markdown(description_html)
    body_parts = [f"# {name}" if name else "# TAPD Story"]
    if markdown:
        body_parts.append(markdown)
    elif description_html.strip():
        body_parts.append(description_html.strip())
    else:
        body_parts.append("_（TAPD 描述为空）_")

    return {
        "title": name or f"story_{long_id}",
        "content": "\n\n".join(body_parts).strip(),
        "description_html": description_html,
        "story": story,
        "workspace_id": str(ws),
        "story_id": str(long_id),
    }
