from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from orchestrator.services.feishu_service import FeishuService
from orchestrator.services.git_service import GitService
from orchestrator.services.llm_service import LLMService
from orchestrator.services.mail_context import prepare_mail_context


class ServiceBoundaryTests(unittest.TestCase):
    def test_mail_context_keeps_skill_relevant_fields_and_bounds_body(self) -> None:
        with patch.dict("os.environ", {"MAIL_CONTEXT_MAX_CHARS": "1000"}, clear=False):
            result = prepare_mail_context({
                "message_id": "m1", "subject": "需求", "sender": "a@example.test",
                "reviewer_name": "张三", "remark": "重点回归", "projects": [{"project": "api"}],
                "body_text": "x" * 1200,
            })
        self.assertEqual(result["message_id"], "m1")
        self.assertEqual(result["projects"], [{"project": "api"}])
        self.assertTrue(result["body_truncated"])
        self.assertIn("mail body truncated", result["body_text"])

    def test_git_service_delegates_snapshot(self) -> None:
        with patch("change_analysis_harness.git_service.GitService.snapshot_repository", return_value={"target_commit": "abc"}) as snapshot:
            result = GitService().snapshot("api", "feature", {"api": {"local_path": ".", "repository": "repo"}})
        self.assertEqual(result, {"target_commit": "abc", "repository": "repo"})
        self.assertEqual(snapshot.call_args.kwargs["repository_name"], "api")

    def test_llm_service_delegates_analysis(self) -> None:
        with patch("orchestrator.task_runner.analyze_with_openai", return_value={"result": {}}):
            result = LLMService().analyze({})
        self.assertIn("coverage_reflection", result)
        self.assertFalse(result["coverage_reflection"]["ok"])
        self.assertIn("analysis_metrics", result)
        self.assertEqual(result["effort"], "medium")
        self.assertTrue(any("CoverageReflector" in str(item) for item in result["result"].get("uncertainties", [])))

    def test_llm_service_passes_business_context_to_agent_sdk(self) -> None:
        payload = {
            "mail_context": {
                "message_id": "mail-1",
                "subject": "release review",
                "sender": "qa@example.test",
                "remark": "focus on cache regression",
                "body_text": "Please analyze the API change.",
            },
            "requirements": [{"title": "cache refresh", "acceptance_criteria": ["stale data is removed"]}],
            "requirement_urls": ["https://docs.example.test/cache-refresh"],
            "repositories": [{
                "project": "api",
                "base_branch": "main",
                "target_branch": "feature/cache-refresh",
                "changed_files": ["src/cache.py"],
                "diff": "diff --git a/src/cache.py b/src/cache.py\n+invalidate_cache()\n",
                "analysis_mode": "Full repository context analysis",
                "change_context": {"changed_files": ["src/cache.py"]},
                "repository_impact": {
                    "repository_scan": {"scan_complete": True},
                    "impact_paths": [["api", "cache service"]],
                },
            }],
        }
        expected = {
            "raw": {"id": "resp_skill_test", "runtime": "openai-agents", "skill_invoked": True},
            "result": {
                "message_type": "final",
                "summary": "skill invoked",
                "findings": ["repository evidence received"],
                "risks": [],
                "test_scope": ["verify cache invalidation"],
                "uncertainties": [],
            },
        }

        with patch(
            "orchestrator.services.agent_sdk_service.AgentSdkAnalysisService.analyze",
            return_value=expected,
        ) as analyze:
            result = LLMService().analyze(payload)

        analyze.assert_called_once()
        agent_payload = analyze.call_args.args[0]
        self.assertEqual(agent_payload["mail_context"]["subject"], "release review")
        self.assertEqual(agent_payload["requirements"][0]["title"], "cache refresh")
        self.assertEqual(agent_payload["repositories"][0]["project"], "api")
        self.assertTrue(agent_payload["repositories"][0]["repository_impact"]["repository_scan"]["scan_complete"])
        self.assertEqual(result["raw"]["runtime"], "openai-agents")
        self.assertTrue(result["raw"]["skill_invoked"])
        self.assertEqual(result["result"]["summary"], "skill invoked")

    def test_llm_service_batches_diff_and_summarizes(self) -> None:
        section = lambda name, text: f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n+{text}\n"
        payload = {
            "mail_context": {"subject": "release review", "body_text": "check the cache"},
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
        self.assertTrue(all(call["mail_context"]["subject"] == "release review" for call in calls))
        self.assertEqual(result["batch_results"][0]["result"]["message_type"], "confirmation")
        self.assertIn("analysis_metrics", result)

    def test_llm_service_batches_by_impact_units(self) -> None:
        section = lambda name, text: f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n+{text}\n"
        payload = {
            "mail_context": {"subject": "release review", "body_text": "check units"},
            "requirements": [],
            "repositories": [{
                "project": "api",
                "commits": [],
                "changed_files": ["a.py", "b.py"],
                "diff": section("a.py", "a" * 100) + section("b.py", "b" * 100),
            }],
            "impact_units": [
                {
                    "id": "unit-0-1",
                    "repository_index": 0,
                    "files": ["a.py"],
                    "applicable_guide_sections": ["evidence-priority"],
                },
                {
                    "id": "unit-0-2",
                    "repository_index": 0,
                    "files": ["b.py"],
                    "applicable_guide_sections": ["test-design"],
                },
            ],
            "effort": "low",
        }
        calls = []

        def analyze_once(value):
            calls.append(value)
            return {"result": {
                "message_type": "confirmation",
                "summary": value.get("analysis_mode", "single"),
                "findings": [],
                "risks": [],
                "test_scope": [],
                "uncertainties": [],
            }}

        service = LLMService(url="http://example.test/analyze")
        with patch.object(service, "_analyze_once", side_effect=analyze_once), patch.dict(
            "os.environ", {"LLM_BATCH_DIFF_BYTES": "20000", "LLM_UNIT_CONCURRENCY": "1"}, clear=False
        ):
            result = service.analyze(payload)
        unit_calls = [call for call in calls if call.get("analysis_mode") == "batch-change-analysis"]
        self.assertEqual(len(unit_calls), 2)
        self.assertEqual(unit_calls[0]["impact_unit"]["id"], "unit-0-1")
        self.assertEqual(unit_calls[1]["impact_unit"]["id"], "unit-0-2")
        self.assertEqual(result["batch_count"], 2)
        self.assertEqual(result["effort"], "low")
        self.assertEqual(len(result["analysis_metrics"]["units"]), 2)

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

    def test_confirmation_message_explicitly_requests_a_reply(self) -> None:
        with patch("orchestrator.feishu_bot.send_message", return_value="om_1") as send, patch.dict(
            "os.environ", {"FEISHU_APP_ID": "app", "FEISHU_APP_SECRET": "secret"}, clear=False
        ):
            FeishuService().send_analysis(
                "task-1",
                {"result": {"message_type": "confirmation", "summary": "review", "risks": [], "test_scope": []}},
                receive_id="ou_1",
                receive_id_type="open_id",
            )
        text = send.call_args.args[5]["text"]
        self.assertIn("\u8bf7\u56de\u590d", text)
        self.assertIn("\u4efb\u52a1\uff1atask-1", text)
