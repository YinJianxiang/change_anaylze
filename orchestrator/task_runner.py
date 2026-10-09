"""Execute one ingested mail task and persist its outcome."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
import urllib.request
from pathlib import Path

from orchestrator.mail_ingest import MessageStore, _parse_projects, load_env_file
from orchestrator.services.feishu_service import FeishuService
from orchestrator.services.git_service import GitService, PermanentTaskError
from change_analysis_harness.selection import build_analysis_planning

from orchestrator.services.analysis_context import enrich_snapshots, llm_snapshots
from orchestrator.services.llm_service import LLMService
from orchestrator.services.mail_context import prepare_mail_context
from orchestrator.services.requirement_service import RequirementService
from orchestrator.time_utils import now_beijing

MAX_RETRIES = 3


def load_skill_instructions() -> str:
    """Compatibility name for the bot-specific analysis instructions."""
    from orchestrator.services.analysis_instructions import load_analysis_instructions

    return load_analysis_instructions()


def _now() -> str:
    return now_beijing()


def load_projects_config(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def git_snapshot(project: str, branch: str, config: dict[str, dict[str, str]]) -> dict[str, object]:
    return GitService().snapshot(project, branch, config)


def analyze_with_openai(input_payload: dict[str, object]) -> dict[str, object]:
    """Run change analysis through the OpenAI Agents SDK compatibility entry point."""
    from orchestrator.services.agent_sdk_service import (
        AgentSdkAnalysisService,
        AgentSdkConfigurationError,
    )

    try:
        return AgentSdkAnalysisService().analyze(input_payload)
    except AgentSdkConfigurationError as error:
        raise PermanentTaskError(str(error)) from error

def send_feishu_card(message_id: str, analysis: dict[str, object]) -> str:
    url = os.environ.get("FEISHU_SEND_URL", "").strip()
    token = os.environ.get("FEISHU_BOT_TOKEN", "").strip()
    if not url or not token:
        raise PermanentTaskError("Missing FEISHU_SEND_URL or FEISHU_BOT_TOKEN")
    result = analysis["result"]
    card = {
        "message_id": message_id,
        "title": "代码变更分析结果",
        "summary": result["summary"],
        "risks": result["risks"],
        "test_scope": result["test_scope"],
        "actions": [
            {"text": "确认成功", "action": "confirm_success", "message_id": message_id},
            {"text": "结果有误", "action": "reject", "message_id": message_id},
        ],
    }
    request = urllib.request.Request(url, data=json.dumps(card, ensure_ascii=False).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    robot_message_id = value.get("message_id") or value.get("data", {}).get("message_id")
    if not robot_message_id:
        raise PermanentTaskError("Feishu response is missing message_id")
    return str(robot_message_id)


def run_task(database: Path, config_path: Path, limit: int | None = None) -> list[dict[str, object]]:
    store = MessageStore(database)
    connection = store.connection
    worker = f"task-runner-{uuid.uuid4().hex}"
    query = "SELECT message_id, payload, retry_count FROM processed_messages WHERE status = 'NEW' ORDER BY processed_at, message_id"
    if limit is not None:
        query += " LIMIT ?"
        rows = connection.execute(query, (limit,)).fetchall()
    else:
        rows = connection.execute(query).fetchall()
    config = load_projects_config(config_path)
    llm_service = LLMService()
    feishu_service = FeishuService()
    requirement_service = RequirementService()
    results: list[dict[str, object]] = []
    for message_id, payload, retry_count in rows:
        now = _now()
        cursor = connection.execute(
            "UPDATE processed_messages SET status='PROCESSING', updated_at=?, locked_at=?, locked_by=?, last_error=NULL "
            "WHERE message_id=? AND status='NEW'",
            (now, now, worker, message_id),
        )
        connection.commit()
        if cursor.rowcount != 1:
            continue
        try:
            value = json.loads(payload)
            projects = value.get("projects", [])
            if not projects and isinstance(value.get("body_text"), str):
                projects = [item.__dict__ for item in _parse_projects(value["body_text"])]
            if not projects:
                raise PermanentTaskError("No project branches found in the mail payload")
            snapshots = [git_snapshot(item["project"], item["branch"], config) for item in projects]
            snapshots, analysis_mode, evidence_warnings = enrich_snapshots(snapshots)
            summary_snapshots = llm_snapshots(snapshots)
            planning = build_analysis_planning(summary_snapshots)
            result = {
                "message_id": message_id,
                "repositories": summary_snapshots,
                "analysis_mode": analysis_mode,
                "evidence_warnings": evidence_warnings,
                "requirement_documents": requirement_service.prepare(value.get("requirement_urls", []))["documents"],
                "selection_plan": planning["selection_plan"],
                "impact_units": planning["impact_units"],
                "effort": planning["effort"],
            }
            analysis_input = {
                "mail_context": prepare_mail_context(value),
                "requirement_urls": value.get("requirement_urls", []),
                "requirement_content": None,
                "repositories": summary_snapshots,
                "analysis_mode": analysis_mode,
                "evidence_warnings": evidence_warnings,
                "effort": planning["effort"],
                "selection_plan": planning["selection_plan"],
                "impact_units": planning["impact_units"],
            }
            result["analysis"] = llm_service.analyze(analysis_input)
            if value.get("routing_error"):
                raise PermanentTaskError(str(value["routing_error"]))
            robot_message_id = feishu_service.send_analysis(
                message_id, result["analysis"], receive_id=str(value.get("receiver_id", "")),
                receive_id_type=str(value.get("receiver_id_type", "")))
            status = "WAITING_CONFIRM" if feishu_service.requires_confirmation(result["analysis"]) else "DONE"
            completed_at = _now() if status == "DONE" else None
            connection.execute(
                "UPDATE processed_messages SET status=?, result_payload=?, robot_message_id=?, updated_at=?, completed_at=?, locked_at=NULL, locked_by=NULL WHERE message_id=?",
                (status, json.dumps(result, ensure_ascii=False), robot_message_id, _now(), completed_at, message_id),
            )
            results.append(result)
        except Exception as error:
            next_status = "FAILED" if isinstance(error, PermanentTaskError) or retry_count + 1 >= MAX_RETRIES else "NEW"
            connection.execute(
                "UPDATE processed_messages SET status=?, retry_count=retry_count+1, last_error=?, updated_at=?, locked_at=NULL, locked_by=NULL WHERE message_id=?",
                (next_status, str(error), _now(), message_id),
            )
            results.append(
                {
                    "message_id": message_id,
                    "status": next_status,
                    "retry_count": retry_count + 1,
                    "error": str(error),
                }
            )
        connection.commit()
    store.close()
    return results


def retry_task(database: Path, message_id: str) -> bool:
    store = MessageStore(database)
    cursor = store.connection.execute(
        "UPDATE processed_messages SET status='NEW', last_error=NULL, locked_at=NULL, locked_by=NULL, updated_at=? "
        "WHERE message_id=? AND status='FAILED'",
        (_now(), message_id),
    )
    store.connection.commit()
    store.close()
    return cursor.rowcount == 1


def handle_confirmation(database: Path, message_id: str, action: str, feedback: str = "") -> bool:
    if action not in {"confirm_success", "reject"}:
        raise ValueError("Unsupported confirmation action")
    store = MessageStore(database)
    status = "DONE" if action == "confirm_success" else "REJECTED"
    completed_at = _now() if status == "DONE" else None
    cursor = store.connection.execute(
        "UPDATE processed_messages SET status=?, feedback=?, completed_at=?, updated_at=? "
        "WHERE message_id=? AND status='WAITING_CONFIRM'",
        (status, feedback, completed_at, _now(), message_id),
    )
    store.connection.commit()
    store.close()
    return cursor.rowcount == 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ingested mail analysis tasks.")
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--config", type=Path, default=Path(".local/projects.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retry-message-id")
    parser.add_argument("--git-check-project")
    parser.add_argument("--git-check-branch")
    args = parser.parse_args()
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.git_check_project or args.git_check_branch:
        if not args.git_check_project or not args.git_check_branch:
            parser.error("--git-check-project and --git-check-branch must be used together")
        config = load_projects_config(args.config)
        snapshot = git_snapshot(args.git_check_project, args.git_check_branch, config)
        snapshot.pop("diff", None)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    elif args.retry_message_id:
        print(json.dumps({"retried": retry_task(args.database, args.retry_message_id)}))
    else:
        print(json.dumps(run_task(args.database, args.config, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

