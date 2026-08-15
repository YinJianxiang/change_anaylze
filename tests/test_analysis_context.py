import json
import unittest
from unittest.mock import patch

from orchestrator.services.analysis_context import enrich_snapshot


class AnalysisContextTests(unittest.TestCase):
    def snapshot(self):
        return {
            "project": "market-admin", "local_path": ".", "merge_base": "base",
            "target_commit": "head", "diff": "patch", "changed_files": ["src/a.py"],
        }

    def test_collects_both_skill_evidence(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            payload = {"repository_scan": {"scan_complete": True}} if "collect_repository_impact" in command[1] else {"changed_files": []}
            return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()

        with patch("orchestrator.services.analysis_context.subprocess.run", side_effect=run):
            result = enrich_snapshot(self.snapshot())
        self.assertEqual(result["analysis_mode"], "Full repository context analysis")
        self.assertIn("change_context", result)
        self.assertIn("repository_impact", result)
        self.assertTrue(any("base...head" in " ".join(call) for call in calls))

    def test_script_failure_degrades_without_losing_snapshot(self):
        failure = type("Result", (), {"returncode": 2, "stdout": "", "stderr": "permission denied"})()
        with patch("orchestrator.services.analysis_context.subprocess.run", return_value=failure):
            result = enrich_snapshot(self.snapshot())
        self.assertEqual(result["diff"], "patch")
        self.assertEqual(result["analysis_mode"], "Diff-only analysis")
        self.assertTrue(result["evidence_warnings"])


if __name__ == "__main__":
    unittest.main()
