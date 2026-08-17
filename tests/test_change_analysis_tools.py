import unittest
from unittest.mock import patch

from orchestrator.services.change_analysis_tools import AnalysisToolError, ChangeAnalysisTools


class ChangeAnalysisToolsTests(unittest.TestCase):
    def setUp(self):
        self.tools = ChangeAnalysisTools({"api": {"local_path": ".", "base_branch": "main"}})
        self.snapshot = {
            "project": "api", "local_path": ".", "base_branch": "main",
            "target_branch": "feature", "base_commit": "base", "merge_base": "merge",
            "target_commit": "head", "changed_files": ["src/a.py"], "diff": "patch",
        }

    @patch("orchestrator.services.change_analysis_tools.GitService.snapshot")
    def test_prepares_only_configured_project(self, snapshot):
        snapshot.return_value = self.snapshot
        result = self.tools.prepare_change_workspace("api", "feature")
        self.assertTrue(result["analysis_id"].startswith("analysis_"))
        self.assertEqual(result["target_commit"], "head")
        with self.assertRaises(AnalysisToolError):
            self.tools.prepare_change_workspace("other", "feature")

    @patch("orchestrator.services.change_analysis_tools.ChangeAnalysisTools._git")
    @patch("orchestrator.services.change_analysis_tools.GitService.snapshot")
    def test_reads_bounded_source_at_target_commit(self, snapshot, git):
        snapshot.return_value = self.snapshot
        git.return_value = "one\ntwo\nthree\n"
        analysis_id = self.tools.prepare_change_workspace("api", "feature")["analysis_id"]
        result = self.tools.read_source_evidence(analysis_id, "src/a.py", 2, 3)
        self.assertEqual(result["content"], "2: two\n3: three")
        with self.assertRaises(AnalysisToolError):
            self.tools.read_source_evidence(analysis_id, "../secret")

    def test_rejects_unknown_session(self):
        with self.assertRaises(AnalysisToolError):
            self.tools.collect_change_context("missing")


if __name__ == "__main__":
    unittest.main()
