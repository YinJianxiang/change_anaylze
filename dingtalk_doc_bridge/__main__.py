"""DingTalk document Bridge: wrap Cursor「钉钉文档」MCP as HTTP for task-worker.

Does NOT use DingTalk OpenAPI AppKey. Calls the same streamable-http MCP gateway
configured in Cursor mcp.json under server name「钉钉文档」.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _mcp_url() -> str:
    url = _env("DINGTALK_MCP_URL")
    if not url:
        raise RuntimeError(
            "DINGTALK_MCP_URL is required. Copy the streamable-http url from "
            "Cursor mcp.json → mcpServers.钉钉文档.url"
        )
    return url


def _bridge_token() -> str:
    return _env("DINGTALK_DOC_BRIDGE_TOKEN") or _env("BRIDGE_TOKEN")


def _parse_tool_payload(result: Any) -> dict[str, Any]:
    """Normalize MCP CallToolResult into {title, markdown}."""
    texts: list[str] = []
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        markdown = str(structured.get("markdown") or structured.get("content") or "").strip()
        title = str(structured.get("title") or "").strip()
        if markdown:
            return {"title": title, "markdown": markdown, "raw": structured}

    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            texts.append(str(text))

    blob = "\n".join(texts).strip()
    if not blob:
        raise RuntimeError(f"MCP get_document_content returned empty content: {result!r}")

    # Prefer JSON object payloads from the DingTalk MCP gateway.
    try:
        parsed = json.loads(blob)
        if isinstance(parsed, dict):
            markdown = str(parsed.get("markdown") or parsed.get("content") or "").strip()
            title = str(parsed.get("title") or "").strip()
            if markdown:
                return {"title": title, "markdown": markdown, "raw": parsed}
    except json.JSONDecodeError:
        pass

    title_match = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', blob)
    title = title_match.group(1).encode("utf-8").decode("unicode_escape") if title_match else ""
    return {"title": title, "markdown": blob, "raw": None}


async def fetch_via_mcp(node_id: str, fmt: str = "markdown") -> dict[str, Any]:
    url = _mcp_url()
    arguments = {"nodeId": node_id, "format": fmt or "markdown"}
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("get_document_content", arguments=arguments)
            if getattr(result, "isError", False):
                raise RuntimeError(f"MCP tool error: {result}")
            parsed = _parse_tool_payload(result)
            return {
                "title": parsed["title"] or node_id,
                "markdown": parsed["markdown"],
                "content": parsed["markdown"],
                "nodeId": node_id,
                "source": "dingtalk_mcp",
            }


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = _bridge_token()
    if not expected:
        return True
    auth = handler.headers.get("Authorization", "")
    return auth == f"Bearer {expected}"


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "DingTalkDocBridge/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dingtalk-doc-bridge] {self.address_string()} - {fmt % args}")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/health", "/healthz"}:
            self._send_json(200, {"ok": True, "service": "dingtalk-doc-bridge"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/v1/doc/content", "/"}:
            self._send_json(404, {"error": "not found"})
            return
        if not _authorized(self):
            self._send_json(401, {"error": "unauthorized"})
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        node_id = str(payload.get("nodeId") or payload.get("node_id") or "").strip()
        fmt = str(payload.get("format") or "markdown").strip() or "markdown"
        if not node_id:
            self._send_json(400, {"error": "nodeId is required"})
            return

        try:
            doc = asyncio.run(fetch_via_mcp(node_id, fmt))
            self._send_json(200, doc)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json(502, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="DingTalk MCP document HTTP bridge")
    parser.add_argument("--host", default=_env("BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_env("BRIDGE_PORT", "8091") or "8091"))
    args = parser.parse_args()

    # Fail fast if MCP URL missing.
    _mcp_url()

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"[dingtalk-doc-bridge] listening on http://{args.host}:{args.port}")
    print("[dingtalk-doc-bridge] POST /v1/doc/content  body={nodeId, format}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dingtalk-doc-bridge] stopped")


if __name__ == "__main__":
    main()
