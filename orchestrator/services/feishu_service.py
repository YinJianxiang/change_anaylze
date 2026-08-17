"""Feishu notification service boundary."""
from __future__ import annotations

import os
from typing import Any


class FeishuService:
    def send_analysis(self, message_id: str, analysis: dict[str, Any], *, receive_id: str = "",
                      receive_id_type: str = "") -> str:
        if not receive_id or receive_id_type != "open_id":
            raise ValueError("Feishu analysis delivery requires a routed open_id")
        from orchestrator.feishu_bot import send_message
        app_id = os.environ.get("FEISHU_APP_ID", "").strip()
        app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
        if not app_id or not app_secret:
            raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
        result = analysis.get("result", analysis)
        text = "\n".join([
            "代码变更分析结果",
            str(result.get("summary", "")),
            "风险：" + "；".join(map(str, result.get("risks", []))),
            "测试范围：" + "；".join(map(str, result.get("test_scope", []))),
            f"任务：{message_id}",
        ])
        return send_message(app_id, app_secret, "open_id", receive_id, "text", {"text": text})
