"""Smoke-test DingTalk doc fetch: Bridge HTTP and/or RequirementService.

Usage (from change_analyze root):
  python -m dingtalk_doc_bridge.smoke_test --node-id "https://alidocs.dingtalk.com/i/nodes/xxxx"
  python -m dingtalk_doc_bridge.smoke_test --node-id "xxxx" --via service
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _post_bridge(node_id: str) -> dict:
    url = os.environ.get("DINGTALK_DOC_BRIDGE_URL", "").strip()
    if not url:
        raise SystemExit("DINGTALK_DOC_BRIDGE_URL is empty")
    token = os.environ.get("DINGTALK_DOC_BRIDGE_TOKEN", "").strip()
    body = json.dumps({"nodeId": node_id, "format": "markdown"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _via_service(node_id: str) -> dict:
    from orchestrator.services.requirement_service import RequirementService

    # Prefer full URL so router classifies as dingtalk_doc.
    url = node_id
    if "://" not in url:
        url = f"https://alidocs.dingtalk.com/i/nodes/{node_id}"
    os.environ.setdefault("REQUIREMENT_FETCH_ENABLED", "true")
    return RequirementService().prepare([url])["documents"][0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test DingTalk document fetch")
    parser.add_argument("--node-id", required=True, help="DingTalk nodeId or full alidocs URL")
    parser.add_argument(
        "--via",
        choices=("bridge", "service", "both"),
        default="bridge",
        help="bridge=HTTP Bridge only; service=RequirementService; both=run both",
    )
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    _load_dotenv(root / args.env_file)
    os.chdir(root)

    modes = ["bridge", "service"] if args.via == "both" else [args.via]
    for mode in modes:
        print(f"\n=== via {mode} ===")
        try:
            if mode == "bridge":
                # health
                health_url = os.environ.get("DINGTALK_DOC_BRIDGE_URL", "").rsplit("/", 2)[0] + "/health"
                try:
                    with urllib.request.urlopen(health_url, timeout=5) as resp:
                        print("health:", resp.read().decode("utf-8"))
                except Exception as exc:  # noqa: BLE001
                    print("health FAILED (is Bridge running?):", exc)
                    raise SystemExit(1) from exc
                doc = _post_bridge(args.node_id)
            else:
                doc = _via_service(args.node_id)
            title = doc.get("title") or (doc.get("meta") or {}).get("title")
            content = doc.get("markdown") or doc.get("content") or ""
            status = doc.get("status", "n/a")
            print("status:", status)
            print("title:", title)
            print("content_chars:", len(content or ""))
            print("content_preview:\n", (content or "")[:500])
            if mode == "service" and status not in {"FETCHED", "PARTIAL"}:
                raise SystemExit(f"service fetch not successful: {doc.get('meta')}")
            if mode == "bridge" and not content:
                raise SystemExit("bridge returned empty content")
            print("OK")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            print("HTTPError", exc.code, detail[:500])
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
