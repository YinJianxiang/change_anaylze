from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from change_analysis_harness.guidance import load_analysis_guidance, load_guide_sections
from change_analysis_harness.guide_match import applicable_guide_sections
from change_analysis_harness.harness import ChangeAnalysisHarness, ChangeAnalysisRequest, main


class TestDesignHandoffTests(unittest.TestCase):
    def test_online_handoff_includes_execution_rules_without_altering_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            snapshot = {"project": "demo", "changed_files": [], "diff": ""}
            git = Mock()
            git.snapshot_repository.return_value = snapshot
            requirements = Mock()
            requirements.prepare.return_value = {"documents": []}
            with patch("change_analysis_harness.harness.enrich_snapshots",
                       return_value=([snapshot], "Diff-only evidence analysis", [])):
                result = ChangeAnalysisHarness(git_service=git, requirement_service=requirements).run(
                    ChangeAnalysisRequest("https://example.invalid/story", "feature", repo)
                )

        handoff = json.loads(json.dumps(result, ensure_ascii=False))
        self.assertEqual(handoff["analysis_input"]["analysis_guidance"], load_analysis_guidance())
        self.assertEqual(handoff["repository"], snapshot)
        self.assertIsNone(handoff["analysis"])

    def test_offline_cli_persists_guidance_in_both_outputs_and_marks_report_as_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, report = root / "input.patch", root / "out.json", root / "report.md"
            source.write_text(
                "diff --git a/demo.txt b/demo.txt\n--- a/demo.txt\n+++ b/demo.txt\n"
                "@@ -1 +1 @@\n-old\n+new\n", encoding="utf-8"
            )
            with patch("change_analysis_harness.harness.GitService.snapshot_repository",
                       side_effect=AssertionError("offline Git access")), \
                 patch("change_analysis_harness.harness.RequirementService.prepare",
                       side_effect=AssertionError("offline requirement access")):
                self.assertEqual(main([
                    "--diff-file", str(source), "--output", str(output),
                    "--report-output", str(report), "--no-archify",
                    "--env-file", str(root / "absent.env"),
                ]), 0)
            full = json.loads(output.read_text(encoding="utf-8"))
            summary = json.loads((root / "out.analysis-input.json").read_text(encoding="utf-8"))
            text = report.read_text(encoding="utf-8")

        self.assertEqual(full["analysis_input"]["analysis_guidance"], summary["analysis_guidance"])
        self.assertNotIn(load_guide_sections(["test-design"]), text)
        self.assertIn("## 必测内容", text)
        self.assertIn("## 待确认与限制", text)
        self.assertIn("待分析", text)
        self.assertIsNone(full["analysis"])

    def test_user_guide_rules_reach_all_impact_units(self):
        for path in ("src/main/java/demo/FooController.java", "src/main/java/demo/FooJob.java",
                     "src/views/TrendChart.vue", "src/api/chart.ts"):
            with self.subTest(path=path):
                text = load_analysis_guidance(applicable_guide_sections([path]))
                for required in ("High", "Medium", "Low", "Trace deleted behavior",
                                 "both sides of a frontend/backend contract", "Preconditions/data",
                                 "expected observable result", "Change-to-test heuristics"):
                    self.assertIn(required, text)
                template = text.split("# Bundled Skill reference: report-template.md", 1)[1]
                headings = ("## 分析范围", "## 仓库上下文", "## 改动与需求摘要", "## 影响链路",
                            "## 调用链影响", "## 风险结论", "## 必测内容", "## 回归范围",
                            "## 需求追踪", "## 建议执行", "## 待确认与限制")
                positions = [template.index(heading) for heading in headings]
                self.assertEqual(positions, sorted(positions))
                self.assertNotIn("## 具体风险", template)
                self.assertNotIn("公式与数据核验", text)

    def test_requirement_completeness_survives_guide_slicing(self):
        for sections in (None, ["evidence-priority"], applicable_guide_sections(["src/views/Page.vue"])):
            with self.subTest(sections=sections):
                text = load_analysis_guidance(sections)
                guide, template = text.split("# Bundled Skill reference: report-template.md", 1)
                self.assertIn("Requirement completeness (Mode B)", guide)
                self.assertIn("not just acceptance points visible in the diff", guide)
                self.assertIn("zero search hits alone does not prove", guide)
                self.assertIn("change_analysis_harness.database_cli", guide)
                self.assertIn("least-privilege read-only account", guide)
                for status in ("已实现", "部分实现", "未实现", "实现与需求不一致", "无法确认", "不适用"):
                    self.assertIn(status, guide)
                    self.assertIn(status, template)
                self.assertIn("本次 diff 未涉及", template)
                self.assertIn("验收点总数", template)

    def test_canonical_documents_keep_user_template_structure(self):
        text = load_analysis_guidance()
        self.assertIn("| ID | 优先级 | 类型 | 前置条件/数据 | 操作 | 预期结果 | 证据 |", text)
        self.assertIn("| 入口/场景 | 前端 | 接口 | 后端 | 数据/外部依赖 | 影响类型 | 证据 |", text)
        self.assertIn("Lowering risk requires evidence, not assumption.", text)
        self.assertIn("未执行，仅建议", text)


if __name__ == "__main__":
    unittest.main()
