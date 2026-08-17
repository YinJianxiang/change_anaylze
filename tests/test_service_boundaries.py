from __future__ import annotations

import unittest
from unittest.mock import patch

from orchestrator.services.feishu_service import FeishuService
from orchestrator.services.git_service import GitService
from orchestrator.services.llm_service import LLMService


class ServiceBoundaryTests(unittest.TestCase):
    def test_git_service_delegates_snapshot(self) -> None:
        with patch("orchestrator.task_runner.git_snapshot", return_value={"target_commit": "abc"}):
            self.assertEqual(GitService().snapshot("api", "feature", {}), {"target_commit": "abc"})

    def test_llm_service_delegates_analysis(self) -> None:
        with patch("orchestrator.task_runner.analyze_with_openai", return_value={"result": {}}):
            self.assertEqual(LLMService().analyze({}), {"result": {}})

    def test_llm_service_batches_diff_and_summarizes(self) -> None:
        section = lambda name, text: f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n+{text}\n"
        payload = {
            "requirements": [],
            "repositories": [{
                "project": "api", "commits": [], "changed_files": ["a.py", "b.py"],
                "diff": section("a.py", "a" * 700) + section("b.py", "b" * 700),
            }],
        }
        calls = []

        def analyze_once(value):
            calls.append(value)
            return {"result": {"summary": value.get("analysis_mode", "single")}}

        service = LLMService(url="http://example.test/analyze")
        with patch.object(service, "_analyze_once", side_effect=analyze_once), patch.dict(
            "os.environ", {"LLM_BATCH_DIFF_BYTES": "1000"}, clear=False
        ):
            result = service.analyze(payload)
        self.assertEqual(len(calls), 3)
        self.assertEqual([call["analysis_mode"] for call in calls],
                         ["batch-change-analysis", "batch-change-analysis", "batch-summary"])
        self.assertEqual(result["batch_count"], 2)
        self.assertEqual(result["batch_results"][0]["changed_files"], ["a.py"])

    def test_llm_service_uses_hierarchical_summary(self) -> None:
        sections = "".join(
            f"diff --git a/{i}.py b/{i}.py\n+{'x' * 700}\n" for i in range(5)
        )
        payload = {"requirements": [], "repositories": [{
            "project": "api", "commits": [], "changed_files": [], "diff": sections,
        }]}
        calls = []

        def analyze_once(value):
            calls.append(value)
            return {"result": {"summary": "ok"}}

        service = LLMService(url="http://example.test/analyze")
        with patch.object(service, "_analyze_once", side_effect=analyze_once), patch.dict(
            "os.environ", {"LLM_BATCH_DIFF_BYTES": "1000", "LLM_SUMMARY_GROUP_SIZE": "2"}, clear=False
        ):
            result = service.analyze(payload)
        summary_calls = [call for call in calls if call.get("analysis_mode") == "batch-summary"]
        self.assertEqual(len(summary_calls), 6)
        self.assertEqual(result["summary_levels"], 3)

    def test_feishu_service_delegates_notification(self) -> None:
        with patch("orchestrator.feishu_bot.send_message", return_value="om_1"), patch.dict(
            "os.environ", {"FEISHU_APP_ID": "app", "FEISHU_APP_SECRET": "secret"}, clear=False
        ):
            self.assertEqual(
                FeishuService().send_analysis("mail-1", {}, receive_id="ou_1", receive_id_type="open_id"),
                "om_1",
            )

    def test_feishu_service_rejects_unrouted_delivery(self) -> None:
        with self.assertRaisesRegex(ValueError, "open_id"):
            FeishuService().send_analysis("mail-1", {})
