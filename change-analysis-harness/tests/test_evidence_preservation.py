"""Full evidence survives summary limits and partial collector failures."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from change_analysis_harness.analysis_context import EvidenceCollectionError, enrich_snapshots
from change_analysis_harness.harness import ChangeAnalysisHarness, ChangeAnalysisRequest, main


class EvidencePreservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".git").mkdir()
        self.snapshot = {
            "project": "demo", "local_path": str(self.root), "merge_base": "base",
            "target_commit": "head", "diff": "diff --git a/Demo.java b/Demo.java\n+你好世界\n",
            "changed_files": ["Demo.java"],
        }
        self.context = {
            "unified_diff": self.snapshot["diff"],
            "changed_files": [{"path": "Demo.java", "change_type": "modified"}],
        }
        self.symbols = [{"symbol": f"method{i}", "file": "Demo.java"} for i in range(7)]
        self.impact = {
            "changed_symbols": self.symbols,
            "symbol_references": [{"path": f"Caller{i}.java"} for i in range(6)],
            "related_tests": [{"path": f"Test{i}.java"} for i in range(5)],
            "impact_slice": {"candidate_edges": [{"callee": f"method{i}"} for i in range(8)]},
        }

    def mocked_collectors(self):
        stack = ExitStack()
        stack.enter_context(patch(
            "change_analysis_harness.harness.GitService.snapshot_repository", return_value=self.snapshot
        ))
        stack.enter_context(patch(
            "change_analysis_harness.harness.RequirementService.prepare",
            return_value={"documents": [{"url": "https://example.invalid/1", "content": "需求", "meta": {"title": "Demo"}}]},
        ))
        stack.enter_context(patch(
            "change_analysis_harness.analysis_context._run_script",
            side_effect=[self.context, self.impact],
        ))
        return stack

    def test_full_evidence_is_retained_while_summary_is_bounded(self):
        original = deepcopy((self.snapshot, self.context, self.impact))
        with self.mocked_collectors():
            result = ChangeAnalysisHarness(max_diff_bytes=10).run(ChangeAnalysisRequest(
                "https://example.invalid/1", "feature", self.root,
            ))
        full = result["repository"]
        summary = result["analysis_input"]["repositories"][0]
        self.assertEqual(full["repository_impact"], self.impact)
        self.assertEqual(full["diff"], self.snapshot["diff"])
        self.assertEqual(full["change_context"]["unified_diff"], self.snapshot["diff"])
        self.assertFalse(full["diff_truncated_for_analysis"])
        self.assertTrue(summary["diff_truncated_for_analysis"])
        self.assertNotIn("unified_diff", summary["change_context"])
        self.assertEqual(summary["repository_impact"]["changed_symbols"][-1], {"_truncated_items": 4})
        self.assertEqual(summary["repository_impact"]["related_tests"][-1], {"_truncated_items": 2})
        self.assertEqual(summary["repository_impact"]["impact_slice"]["candidate_edges"][-1], {"_truncated_items": 5})
        self.assertTrue(result["analysis_input"]["evidence_warnings"])
        self.assertEqual(result["evidence_warnings"], [])
        self.assertEqual(result["analysis_input"]["evidence_reference"]["json_pointer"], "/repository")
        self.assertEqual((self.snapshot, self.context, self.impact), original)

    def test_cli_saves_full_evidence_summary_and_correct_report_paths(self):
        output = self.root / "artifacts" / "custom.json"
        report = self.root / "report.md"
        with self.mocked_collectors():
            status = main([
                "https://example.invalid/1", "feature", "--repo", str(self.root),
                "--env-file", str(self.root / "absent.env"), "--output", str(output),
                "--report-output", str(report), "--max-diff-bytes", "10",
            ])
        self.assertEqual(status, 0)
        full = json.loads(output.read_text(encoding="utf-8"))
        summary_path = self.root / "artifacts" / "custom.analysis-input.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary, full["analysis_input"])
        self.assertEqual(Path(summary["evidence_reference"]["path"]), output.resolve())
        self.assertEqual(full["repository"]["repository_impact"]["changed_symbols"], self.symbols)
        self.assertEqual(full["repository"]["diff"], self.snapshot["diff"])
        markdown = report.read_text(encoding="utf-8")
        self.assertIn("method6", markdown)
        self.assertIn("Changed symbols: **7**", markdown)
        self.assertIn(str(output.resolve()), markdown)
        self.assertIn(str(summary_path.resolve()), markdown)

    def test_stdout_retains_full_evidence_without_creating_summary_files(self):
        stdout = io.StringIO()
        with self.mocked_collectors(), redirect_stdout(stdout):
            main([
                "https://example.invalid/1", "feature", "--repo", str(self.root),
                "--env-file", str(self.root / "absent.env"), "--no-report",
            ])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["repository"]["repository_impact"], self.impact)
        self.assertEqual(result["analysis_input"]["evidence_reference"], {"json_pointer": "/repository"})
        self.assertEqual(list(self.root.glob("*.json")), [])

    def test_impact_failure_preserves_collected_git_context(self):
        failures = [EvidenceCollectionError("invalid JSON"), OSError("unavailable"),
                    subprocess.TimeoutExpired("impact", 60)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch(
                "change_analysis_harness.analysis_context._run_script",
                side_effect=[self.context, failure],
            ):
                snapshots, mode, warnings = enrich_snapshots([self.snapshot])
            self.assertEqual(snapshots[0]["change_context"], self.context)
            self.assertIsNone(snapshots[0]["repository_impact"])
            self.assertEqual(snapshots[0]["diff"], self.snapshot["diff"])
            self.assertEqual(mode, "Partial evidence analysis")
            self.assertIn("repository_impact:", warnings[0])

    def test_context_failure_still_attempts_and_retains_impact(self):
        with patch("change_analysis_harness.analysis_context._run_script", side_effect=[
            EvidenceCollectionError("context failed"), self.impact,
        ]) as collector:
            snapshots, mode, warnings = enrich_snapshots([self.snapshot])
        self.assertEqual(collector.call_count, 2)
        self.assertIsNone(snapshots[0]["change_context"])
        self.assertEqual(snapshots[0]["repository_impact"], self.impact)
        self.assertEqual(mode, "Partial evidence analysis")
        self.assertIn("change_context:", warnings[0])

    def test_both_failures_preserve_diff_and_both_errors(self):
        with patch("change_analysis_harness.analysis_context._run_script", side_effect=[
            EvidenceCollectionError("context failed"), EvidenceCollectionError("impact failed"),
        ]):
            snapshots, mode, warnings = enrich_snapshots([self.snapshot])
        self.assertEqual(mode, "Diff-only evidence analysis")
        self.assertEqual(len(warnings), 2)
        self.assertIsNone(snapshots[0]["change_context"])
        self.assertIsNone(snapshots[0]["repository_impact"])
        self.assertEqual(snapshots[0]["diff"], self.snapshot["diff"])


if __name__ == "__main__":
    unittest.main()
