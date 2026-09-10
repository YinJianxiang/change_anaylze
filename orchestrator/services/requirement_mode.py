"""Select Mode A / Mode B based on prefetched requirement documents."""

from __future__ import annotations

from typing import Any


def has_fetched_requirements(input_payload: dict[str, Any] | dict[str, object]) -> bool:
    for key in ("requirements", "requirement_documents"):
        value = input_payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            status = str(item.get("status") or "")
            if content and status in {"FETCHED", "PARTIAL"}:
                return True
    return False


def requirement_mode_instruction(input_payload: dict[str, Any] | dict[str, object]) -> str:
    if has_fetched_requirements(input_payload):
        return (
            "需求模式：Mode B（需求对照）。使用 analysis_request 中已拉取的 requirement 正文"
            "（status=FETCHED/PARTIAL 的 content），对照代码变更做需求追踪；不要自行访问外部 URL。"
        )
    return (
        "需求模式：Mode A（仅代码变更）。参考文档仅提供 URL 或未拉取正文，"
        "不要假设已读取外部需求文档。"
    )
