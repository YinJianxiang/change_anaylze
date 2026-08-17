"""Delivery boundary for a DingTalk enterprise Agent or workflow trigger."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class DingTalkAgentService:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = url or os.environ.get("DINGTALK_AGENT_TRIGGER_URL", "").strip()
        self.token = token or os.environ.get("DINGTALK_AGENT_TRIGGER_TOKEN", "").strip()

    def dispatch(self, *, task_id: str, access_token: str, reviewer_name: str,
                 dingtalk_user_id: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            raise RuntimeError("DINGTALK_AGENT_TRIGGER_URL is not configured")
        payload = {
            "event_type": "change_analysis.requested",
            "task_id": task_id,
            "agent_access_token": access_token,
            "recipient": {"name": reviewer_name, "dingtalk_user_id": dingtalk_user_id},
            "input": {
                "subject": context.get("subject", ""),
                "sender": context.get("sender", ""),
                "projects": context.get("projects", []),
                "requirement_urls": context.get("requirement_urls", []),
                "remark": context.get("remark", ""),
            },
            "prompt": (
                "Use the analyze-change-test-scope skill. First call get_mail_analysis_task "
                "with task_id and agent_access_token, then collect repository evidence through "
                "the change-analysis MCP tools and return the report to this DingTalk user."
            ),
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {"accepted": True}
