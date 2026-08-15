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

    def test_feishu_service_delegates_notification(self) -> None:
        with patch("orchestrator.task_runner.send_feishu_card", return_value="om_1"):
            self.assertEqual(FeishuService().send_analysis("mail-1", {}), "om_1")
