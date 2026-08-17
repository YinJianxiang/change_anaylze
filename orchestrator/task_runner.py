"""Execute one ingested mail task and persist its outcome."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from orchestrator.mail_ingest import MessageStore, _parse_projects, load_env_file
from orchestrator.services.feishu_service import FeishuService
from orchestrator.services.git_service import GitService
from orchestrator.services.analysis_context import enrich_snapshots, llm_snapshots
from orchestrator.services.llm_service import LLMService
from orchestrator.services.requirement_service import RequirementService
from orchestrator.time_utils import now_beijing

MAX_RETRIES = 3
SKILL_PATH = Path(__file__).resolve().parents[1] / "analyze-change-test-scope" / "skills" / "analyze-change-test-scope" / "SKILL.md"


class PermanentTaskError(ValueError):
    pass


def _now() -> str:
    return now_beijing()


def load_projects_config(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def git_snapshot(project: str, branch: str, config: dict[str, dict[str, str]]) -> dict[str, object]:
    settings = config.get(project)
    if not settings:
        raise PermanentTaskError(f"Missing repository mapping for project: {project}")
    local_path = Path(settings.get("local_path", ""))
    repository = settings.get("repository", "")
    base_branch = settings.get("base_branch", "master")
    if not local_path.exists():
        raise PermanentTaskError(f"Repository path does not exist for {project}: {local_path}")
    git_env = os.environ.copy()
    git_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    token = os.environ.get("GIT_PERSONAL_ACCESS_TOKEN", "").strip()
    username = os.environ.get("GIT_USERNAME", "oauth2").strip()
    auth_header = os.environ.get("GIT_AUTH_HEADER", "").strip()
    if token and not auth_header:
        credentials = base64.b64encode(f"{username}:{token}".encode()).decode()
        auth_header = f"Authorization: Basic {credentials}"
    def run(*args: str) -> str:
        command = ["git"]
        if auth_header:
            command.extend(["-c", f"http.extraHeader={auth_header}"])
        command.extend(args)
        completed = subprocess.run(
            command,
            cwd=local_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=git_env,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
        return (completed.stdout or "").strip()
    run("fetch", "origin", base_branch, branch)
    base_ref = f"origin/{base_branch}"
    target_ref = f"origin/{branch}"
    base_commit = run("rev-parse", base_ref)
    target_commit = run("rev-parse", target_ref)
    current_merge_base = run("merge-base", base_ref, target_ref)
    merge_commit = ""
    merge_base = current_merge_base
    if current_merge_base == target_commit:
        # Once a feature branch is merged, its tip becomes an ancestor of the
        # current base branch. Recover the pre-merge first parent so the
        # feature's original changes remain analyzable.
        for commit in run("rev-list", "--first-parent", "--merges", base_ref).splitlines():
            parents = run("show", "-s", "--format=%P", commit).split()
            if target_commit in parents[1:]:
                merge_commit = commit
                merge_base = run("merge-base", parents[0], target_commit)
                break
    diff = run("diff", "--no-ext-diff", merge_base, target_commit)
    files = run("diff", "--name-only", merge_base, target_commit).splitlines()
    commit_lines = run(
        "log", "--reverse", "--format=%H%x09%an%x09%aI%x09%s", f"{merge_base}..{target_commit}"
    ).splitlines()
    commits = []
    for line in commit_lines:
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append({"commit": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return {
        "project": project, "repository": repository, "local_path": str(local_path),
        "base_branch": base_branch, "target_branch": branch,
        "base_commit": base_commit, "merge_base": merge_base, "target_commit": target_commit,
        "merge_commit": merge_commit or None,
        "commits": commits, "changed_files": files, "diff": diff,
    }


def analyze_with_openai(input_payload: dict[str, object]) -> dict[str, object]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise PermanentTaskError("Missing OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", "gpt-5")
    if not SKILL_PATH.exists():
        raise PermanentTaskError(f"Analysis skill not found: {SKILL_PATH}")
    skill_instructions = SKILL_PATH.read_text(encoding="utf-8")
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "test_scope": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "findings", "risks", "test_scope", "uncertainties"],
        "additionalProperties": False,
    }
    analysis_mode = str(input_payload.get("analysis_mode", ""))
    mode_instruction = ""
    if analysis_mode == "batch-change-analysis":
        mode_instruction = (
            "\nThis is one batch of a larger change. Be concise: return at most 5 findings, "
            "5 risks, 8 test items, and 5 uncertainties. Analyze only the supplied files; "
            "do not restate repository metadata or produce a final cross-batch report."
        )
    elif analysis_mode == "batch-summary":
        mode_instruction = (
            "\nThis is the final aggregation. Deduplicate the supplied batch results, preserve "
            "cross-module risks, and return one concise final report."
        )
    body = {
        "model": model,
        "input": [{"role": "system", "content": skill_instructions + "\nUse the supplied repository evidence and distinguish facts from inferences. Return only the requested JSON schema." + mode_instruction},
                  {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)}],
        "text": {"format": {"type": "json_schema", "name": "change_analysis", "strict": True, "schema": schema}},
        "max_output_tokens": int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "4000")),
    }
    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "low").strip()
    if reasoning_effort:
        body["reasoning"] = {"effort": reasoning_effort}
    debug_dir = os.environ.get("LLM_DEBUG_DUMP_DIR", "").strip()
    if debug_dir:
        debug_path = Path(debug_dir)
        debug_path.mkdir(parents=True, exist_ok=True)
        debug_file = debug_path / f"llm-request-{uuid.uuid4().hex}.json"
        debug_file.write_text(json.dumps({
            "model": model,
            "payload_bytes": len(json.dumps(body, ensure_ascii=False).encode("utf-8")),
            "request": body,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"LLM debug request written to {debug_file}", file=sys.stderr)
    responses_url = os.environ.get("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses").rstrip("/")
    if responses_url.endswith("/v1"):
        responses_url += "/responses"
    request = urllib.request.Request(
        responses_url,
        data=json.dumps(body).encode("utf-8"), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = json.load(response)
    text = raw.get("output_text")
    if not text:
        for item in raw.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text")
                    break
    analysis = json.loads(text or "")
    if not isinstance(analysis, dict) or any(key not in analysis for key in schema["required"]):
        raise PermanentTaskError("OpenAI response does not match the analysis schema")
    return {"model": model, "requested_at": _now(), "raw": raw, "result": analysis}


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
    git_service = GitService()
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
            snapshots = [git_service.snapshot(item["project"], item["branch"], config) for item in projects]
            snapshots, analysis_mode, evidence_warnings = enrich_snapshots(snapshots)
            result = {
                "message_id": message_id,
                "repositories": llm_snapshots(snapshots),
                "analysis_mode": analysis_mode,
                "evidence_warnings": evidence_warnings,
                "requirement_documents": requirement_service.prepare(value.get("requirement_urls", []))["documents"],
            }
            analysis_input = {
                "requirement_urls": value.get("requirement_urls", []),
                "requirement_content": None,
                "repositories": snapshots,
                "analysis_mode": analysis_mode,
                "evidence_warnings": evidence_warnings,
            }
            result["analysis"] = llm_service.analyze(analysis_input)
            if value.get("routing_error"):
                raise PermanentTaskError(str(value["routing_error"]))
            robot_message_id = feishu_service.send_analysis(
                message_id, result["analysis"], receive_id=str(value.get("receiver_id", "")),
                receive_id_type=str(value.get("receiver_id_type", "")))
            connection.execute(
                "UPDATE processed_messages SET status='WAITING_CONFIRM', result_payload=?, robot_message_id=?, updated_at=?, locked_at=NULL, locked_by=NULL WHERE message_id=?",
                (json.dumps(result, ensure_ascii=False), robot_message_id, _now(), message_id),
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
