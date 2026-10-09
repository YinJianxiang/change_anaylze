"""Deterministic coverage checks for analysis results."""
from __future__ import annotations

from typing import Any

REQUIRED_RESULT_KEYS = (
    "summary",
    "findings",
    "risks",
    "test_scope",
    "uncertainties",
)

HIGH_RISK_MARKERS = ("p0", "p1", "高", "严重", "阻断")


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        parts = [
            str(value.get("summary") or ""),
            str(value.get("text") or ""),
            str(value.get("title") or ""),
            str(value.get("description") or ""),
            str(value.get("risk") or ""),
            str(value.get("id") or ""),
        ]
        return " ".join(part for part in parts if part)
    return str(value)


def _has_evidence_refs(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    refs = item.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return False
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("file") and (ref.get("line") is not None or ref.get("scope_id") or ref.get("kind")):
            return True
    return False


def _looks_high_risk(item: object) -> bool:
    text = _text(item).lower()
    if any(marker in text for marker in HIGH_RISK_MARKERS):
        return True
    if isinstance(item, dict):
        level = str(item.get("level") or item.get("priority") or item.get("severity") or "").lower()
        return level in {"p0", "p1", "high", "critical"}
    return False


def _claims_confirmed_without_anchor(item: object) -> bool:
    text = _text(item)
    lowered = text.lower()
    confirmed_markers = ("已确认", "confirmed caller", "confirmed_caller", "确定调用")
    candidate_markers = ("候选", "candidate", "待确认", "需确认")
    if not any(marker.lower() in lowered or marker in text for marker in confirmed_markers):
        return False
    if any(marker.lower() in lowered or marker in text for marker in candidate_markers):
        return False
    if isinstance(item, dict):
        refs = item.get("evidence_refs") or []
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, dict) and str(ref.get("kind") or "") in {"confirmed_caller", "ast", "diff"}:
                return False
        return True
    return True


def reflect_analysis_result(
    result: dict[str, Any] | None,
    *,
    effort: str = "medium",
    strict: bool | None = None,
) -> dict[str, Any]:
    """Return coverage gaps for a bot/Codex structured analysis result."""
    payload = result if isinstance(result, dict) else {}
    analysis = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    use_strict = bool(strict) if strict is not None else effort == "high"
    gaps: list[str] = []

    for key in REQUIRED_RESULT_KEYS:
        if key not in analysis:
            gaps.append(f"missing_section:{key}")
        elif key != "summary" and not _as_list(analysis.get(key)):
            if use_strict or key in {"findings", "test_scope", "uncertainties"}:
                gaps.append(f"empty_section:{key}")

    findings = _as_list(analysis.get("findings"))
    tests = _as_list(analysis.get("test_scope"))
    risks = _as_list(analysis.get("risks"))

    if use_strict or effort != "low":
        for index, item in enumerate(findings):
            if isinstance(item, dict) and not _has_evidence_refs(item):
                gaps.append(f"finding_missing_evidence_refs:{index}")
            if _claims_confirmed_without_anchor(item):
                gaps.append(f"unanchored_confirmed_claim:finding:{index}")
        for index, item in enumerate(tests):
            if isinstance(item, dict) and not _has_evidence_refs(item):
                gaps.append(f"test_missing_evidence_refs:{index}")

    high_risks = [item for item in risks if _looks_high_risk(item)]
    if high_risks and not tests:
        gaps.append("high_risk_without_tests")

    for index, item in enumerate(findings + risks):
        if _claims_confirmed_without_anchor(item):
            gap = f"candidate_marked_confirmed:{index}"
            if gap not in gaps:
                gaps.append(gap)

    return {
        "ok": not gaps,
        "gaps": gaps,
        "effort": effort,
        "strict": use_strict,
    }


def merge_reflection_into_analysis(
    analysis: dict[str, Any],
    reflection: dict[str, Any],
) -> dict[str, Any]:
    """Attach reflector gaps into uncertainties / evidence_warnings without dropping results."""
    updated = dict(analysis)
    result = updated.get("result")
    if isinstance(result, dict):
        result = dict(result)
    else:
        result = {}
        for key in REQUIRED_RESULT_KEYS:
            if key in updated:
                result[key] = updated[key]
        if "message_type" in updated:
            result["message_type"] = updated["message_type"]
        if "summary" in updated and "summary" not in result:
            result["summary"] = updated["summary"]

    gaps = [str(item) for item in reflection.get("gaps") or []]
    if gaps:
        uncertainties = list(_as_list(result.get("uncertainties")))
        for gap in gaps:
            note = f"CoverageReflector: {gap}"
            if note not in uncertainties:
                uncertainties.append(note)
        result["uncertainties"] = uncertainties
        warnings = list(_as_list(updated.get("evidence_warnings")))
        warning = "CoverageReflector found gaps that need confirmation"
        if warning not in warnings:
            warnings.append(warning)
        updated["evidence_warnings"] = warnings
    updated["coverage_reflection"] = reflection
    updated["result"] = result
    return updated
