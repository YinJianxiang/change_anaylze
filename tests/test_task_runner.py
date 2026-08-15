from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.mail_ingest import MessageStore
from orchestrator.task_runner import git_snapshot, handle_confirmation, retry_task, run_task


def insert_task(database: Path, message_id: str = "<task@example.test>") -> None:
    store = MessageStore(database)
    payload = {"message_id": message_id, "projects": [{"project": "api", "branch": "feature"}], "requirement_urls": ["https://docs.test/1"]}
    store.connection.execute(
        "INSERT INTO processed_messages(message_id, processed_at, payload, status, retry_count) VALUES (?, ?, ?, 'NEW', 0)",
        (message_id, "2026-08-13T00:00:00+00:00", json.dumps(payload)),
    )
    store.connection.commit()
    store.close()


class TaskRunnerTests(unittest.TestCase):
    def test_git_output_uses_utf8_with_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            def fake_run(command, **kwargs):
                calls.append(kwargs)
                output = "base" if "merge-base" in command else "head" if "rev-parse" in command else "中文文件.java" if "--name-only" in command else ""
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            config = {"api": {"local_path": temp_dir, "repository": "repo", "base_branch": "master"}}
            with patch("orchestrator.task_runner.subprocess.run", side_effect=fake_run):
                result = git_snapshot("api", "feature", config)
            self.assertEqual(result["changed_files"], ["中文文件.java"])
            self.assertTrue(all(call["encoding"] == "utf-8" and call["errors"] == "replace" for call in calls))

    def test_git_snapshot_recovers_pre_merge_base_for_merged_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            def fake_run(command, **kwargs):
                joined = " ".join(command)
                outputs = {
                    "rev-parse origin/master": "latest-master\n",
                    "rev-parse origin/feature": "target\n",
                    "merge-base origin/master origin/feature": "target\n",
                    "rev-list --first-parent --merges origin/master": "merge\n",
                    "show -s --format=%P merge": "master-before target\n",
                    "merge-base master-before target": "historical-base\n",
                    "diff --name-only historical-base target": "changed.py\n",
                    "diff --no-ext-diff historical-base target": "patch\n",
                    "log --reverse --format=%H%x09%an%x09%aI%x09%s historical-base..target": "c1\tAlice\t2026-08-10T10:00:00+08:00\tchange one\n",
                }
                output = next((value for suffix, value in outputs.items() if joined.endswith(suffix)), "")
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            config = {"api": {"local_path": temp_dir, "repository": "repo", "base_branch": "master"}}
            with patch("orchestrator.task_runner.subprocess.run", side_effect=fake_run):
                result = git_snapshot("api", "feature", config)
            self.assertEqual(result["merge_base"], "historical-base")
            self.assertEqual(result["base_commit"], "latest-master")
            self.assertEqual(result["merge_commit"], "merge")
            self.assertEqual(result["changed_files"], ["changed.py"])
            self.assertEqual(result["commits"][0]["commit"], "c1")

    def test_git_pat_is_passed_as_process_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            commands = []
            def fake_run(command, **kwargs):
                commands.append(command)
                output = "base" if "merge-base" in command else "head" if "rev-parse" in command else ""
                return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            config = {"api": {"local_path": temp_dir, "repository": "https://git.test/api.git", "base_branch": "master"}}
            with patch.dict("os.environ", {"GIT_USERNAME": "bot", "GIT_PERSONAL_ACCESS_TOKEN": "secret-token"}, clear=False), patch("orchestrator.task_runner.subprocess.run", side_effect=fake_run):
                result = git_snapshot("api", "feature", config)
            self.assertTrue(all("http.extraHeader=Authorization: Basic " in command[2] for command in commands))
            self.assertNotIn("secret-token", json.dumps(result))

    def test_success_waits_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "mail.sqlite3"
            config = root / "projects.json"
            config.write_text(json.dumps({"api": {"local_path": str(root), "repository": "repo", "base_branch": "master"}}))
            insert_task(database)
            snapshot = {"project": "api", "merge_base": "base", "target_commit": "head", "diff": "diff"}
            analysis = {"result": {"summary": "ok", "risks": [], "test_scope": []}}
            with patch("orchestrator.task_runner.git_snapshot", return_value=snapshot), patch("orchestrator.task_runner.analyze_with_openai", return_value=analysis), patch("orchestrator.task_runner.send_feishu_card", return_value="robot-1"):
                self.assertEqual(len(run_task(database, config)), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, robot_message_id FROM processed_messages").fetchone(), ("WAITING_CONFIRM", "robot-1"))
            connection.close()
            self.assertTrue(handle_confirmation(database, "<task@example.test>", "confirm_success", "approved"))
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, feedback FROM processed_messages").fetchone(), ("DONE", "approved"))
            connection.close()

    def test_permanent_failure_and_manual_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "mail.sqlite3"
            insert_task(database)
            run_task(database, root / "missing.json")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, retry_count FROM processed_messages").fetchone(), ("FAILED", 1))
            connection.close()
            self.assertTrue(retry_task(database, "<task@example.test>"))

    def test_reject_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            insert_task(database)
            connection = sqlite3.connect(database)
            connection.execute("UPDATE processed_messages SET status='WAITING_CONFIRM'")
            connection.commit()
            connection.close()
            self.assertTrue(handle_confirmation(database, "<task@example.test>", "reject", "missing evidence"))
            self.assertFalse(handle_confirmation(database, "<task@example.test>", "reject", "again"))


if __name__ == "__main__":
    unittest.main()
