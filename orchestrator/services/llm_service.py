"""LLM analysis service boundary."""
from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.request
from pathlib import Path
from typing import Any


class LLMService:
    def __init__(self, url: str | None = None) -> None:
        configured = url or os.environ.get("LLM_SERVER_URL", "")
        self.url = configured.rstrip("/") if configured else None

    def analyze(self, input_payload: dict[str, object]) -> dict[str, Any]:
        if self.url is None:
            from orchestrator.task_runner import analyze_with_openai
            return analyze_with_openai(input_payload)
        data = json.dumps(input_payload, ensure_ascii=False).encode("utf-8")
        debug_dir = os.environ.get("LLM_DEBUG_DUMP_DIR", "").strip()
        if debug_dir:
            directory = Path(debug_dir)
            directory.mkdir(parents=True, exist_ok=True)
            debug_file = directory / f"llm-http-request-{uuid.uuid4().hex}.json"
            debug_file.write_text(json.dumps({
                "url": self.url,
                "payload_bytes": len(data),
                "request": input_payload,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"LLM debug request written to {debug_file}", file=sys.stderr)
        request = urllib.request.Request(self.url, data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("LLM server returned a non-object response")
        return value
