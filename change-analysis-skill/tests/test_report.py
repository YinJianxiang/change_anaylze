from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from change_analysis_skill.report import default_report_path, render_markdown, requirement_directory_name


class ReportRenderingTests(unittest.TestCase):

    def test_report_path_is_grouped_by_requirement(self) -> None:
        title = "\u7d20\u6750\u6392\u884c\u699c\u589e\u52a0\u5386\u53f2\u5168\u57df GMV \u53ca\u6df7\u5408\u6d88\u8017"
        result = {
            "request": {"requirement_url": "https://example.invalid/story/1"},
            "requirement_documents": [{
                "meta": {
                    "title": title,
                    "story_id": "1166882899001025101",
                }
            }],
        }
        directory = requirement_directory_name(result)
        path = default_report_path(result, Path("reporter"))

        expected = f"{title}_1166882899001025101"
        self.assertEqual(directory, expected)
        self.assertEqual(path, Path("reporter") / expected / "change-analysis-report.md")

    def test_render_markdown_is_utf8_friendly_and_contains_evidence(self) -> None:
        result = {
            "request": {
                "requirement_url": "https://example.invalid/story/1",
                "repository": "D:/repo",
                "branch": "feature/demo",
                "base_branch": "master",
            },
            "requirement_documents": [{
                "content": "# \u7d20\u6750\u6392\u884c\u699c\n\n\u663e\u793a\u5386\u53f2\u5168\u57df GMV\u3002",
                "meta": {"title": "\u7d20\u6750\u6392\u884c\u699c\u9700\u6c42"},
            }],
            "repository": {
                "repository": "D:/repo",
                "target_branch": "feature/demo",
                "base_branch": "master",
                "merge_base": "abc123",
                "target_commit": "def456",
                "changed_files": ["src/Demo.java"],
                "diff_truncated_for_analysis": False,
                "change_context": {
                    "changed_files": [{"path": "src/Demo.java", "change_type": "modified", "added_lines": 2, "deleted_lines": 1}],
                    "source": "range:abc...def",
                },
                "repository_impact": {
                    "analysis_scope": "changed_files_and_changed_ast_scopes_only",
                    "changed_symbols": [{"qualified_name": "Demo.calculate", "kind": "method", "file": "src/Demo.java", "line": 3, "change_type": "modified"}],
                    "java_ast": {"files_parsed": 1, "files": []},
                    "impact_slice": {"graph_quality": {"confirmed_edges": 0, "candidate_references": 1, "ast_calls_from_changed_scopes": 1, "unresolved_symbols": 0}},
                    "analysis_limits": ["\u4ec5\u5206\u6790\u53d8\u66f4\u6587\u4ef6"],
                    "symbol_references": ["Demo.calculate"],
                    "database_dependencies": [],
                    "frontend_consumers": [],
                    "backend_callers": [],
                    "related_tests": [],
                    "repository_scan": {"whole_repository_ast_scan": False},
                },
            },
            "analysis_mode": "Changed-scope AST evidence analysis",
            "evidence_warnings": [],
            "effort": "medium",
            "selection_plan": {
                "files": [{
                    "path": "src/Demo.java",
                    "status": "included",
                    "ast_scope_count": 1,
                    "candidate_reference_count": 0,
                    "reasons": ["Included in changed-scope analysis"],
                }]
            },
            "impact_units": [{
                "id": "unit-0-1",
                "label": "demo:Demo",
                "files": ["src/Demo.java"],
                "diff_byte_estimate": 12,
                "applicable_guide_sections": ["evidence-priority", "test-design"],
            }],
            "status": "READY_FOR_CODEX",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.md"
            output.write_text(render_markdown(result), encoding="utf-8")
            text = output.read_text(encoding="utf-8")

        self.assertIn("# 代码变更测试范围报告", text)
        self.assertIn("\u7d20\u6750\u6392\u884c\u699c\u9700\u6c42", text)
        self.assertIn("Demo.calculate", text)
        self.assertIn("selection_plan 与 impact_units", text)
        self.assertIn("unit-0-1", text)
        self.assertNotIn("?", text)

    def test_template_sections_and_headers_follow_order(self) -> None:
        import re
        text = render_markdown({"requirement_documents": [{"content": "正文\n## 不应成为报告章节"}]})
        self.assertEqual(re.findall(r"^## (.+)$", text, re.MULTILINE), [
            "分析范围", "仓库上下文", "改动与需求摘要", "影响链路", "调用链影响",
            "风险结论", "必测内容", "回归范围", "需求追踪（仅 Mode B）", "建议执行", "待确认与限制",
        ])
        for header in [
            "| 入口/场景 | 前端 | 接口 | 后端 | 数据/外部依赖 | 影响类型 | 证据 |",
            "| 变更对象 | 直接调用方 | 间接业务场景 | 关联接口 | 关联数据 | 关联测试 | 证据 |",
            "| 风险 | 等级 | 原因 | 缓解/验证方式 |",
            "| ID | 优先级 | 类型 | 前置条件/数据 | 操作 | 预期结果 | 证据 |",
            "| 需求 ID | 验收点 | 实现证据 | 测试证据/建议 | 状态 |",
        ]:
            self.assertIn(header, text)
        self.assertIn("> ## 不应成为报告章节", text)

    def test_mode_b_requires_actual_nonempty_body(self) -> None:
        for content in [None, "", " \n\t", {}, []]:
            with self.subTest(content=content):
                text = render_markdown({"request": {"requirement_url": "https://example.invalid"},
                                        "requirement_documents": [{"meta": {"title": "仅标题"}, "content": content}]})
                self.assertIn("模式：Mode A（仅代码变更）", text)
                self.assertIn("需求缺口", text)
                self.assertNotIn("## 需求追踪", text)
                self.assertIn("需求推断：待分析，置信度未评估", text)
        text = render_markdown({"requirement_documents": [{"content": "真实需求正文"}]})
        self.assertIn("模式：Mode B（需求对照）", text)
        self.assertIn("## 需求追踪（仅 Mode B）", text)
        self.assertIn("需求与实现匹配情况待分析", text)

    def test_diff_only_notice_and_changed_scope_scan_boundary(self) -> None:
        text = render_markdown({"analysis_mode": "Diff-only evidence analysis"})
        self.assertIn("未获取完整仓库，无法确认所有间接调用方和完整回归范围。", text)
        self.assertIn("全项目源码扫描完成：否", text)
        text = render_markdown({"analysis_mode": "Changed-scope AST evidence analysis", "repository": {
            "repository_impact": {"analysis_scope": "changed_files_and_changed_ast_scopes_only",
                                  "repository_scan": {"whole_repository_ast_scan": True, "scan_complete": True}}}})
        self.assertIn("全项目源码扫描完成：否", text)
        self.assertNotIn("Full repository context analysis", text)
        self.assertIn("不能视为全仓库扫描", text)

    def test_fetch_failure_and_truncation_are_visible_without_losing_mode_b(self) -> None:
        text = render_markdown({"analysis_mode": "Diff-only evidence analysis",
            "request": {"repository": "D:/repo"},
            "repository": {"diff_truncated_for_analysis": False},
            "requirement_documents": [
                {"content": "部分正文", "status": "PARTIAL", "meta": {"truncated": True}},
                {"status": "ERROR", "error": "fetch failed"},
            ]})
        self.assertIn("模式：Mode B", text)
        self.assertIn("仓库来源：现有本地仓库", text)
        self.assertIn("diff 截断：否", text)
        for evidence in ["PARTIAL", "truncated", "ERROR", "fetch failed", "部分失败或截断不代表完整需求"]:
            self.assertIn(evidence, text)

    def test_unconfirmed_findings_remain_evidence_inside_details(self) -> None:
        text = render_markdown({"repository": {"risk_scan": {"findings": [{"rule_id": "SEC002", "severity": "high",
            "file": "src/demo.py", "line": 2, "message": "候选提示"}], "excluded_files": ["vendor/x.py"]}},
            "selection_plan": {"files": [{"path": "src/demo.py"}]}, "impact_units": [{"id": "unit-1"}]})
        main, supplement = text.split("<details>", 1)
        self.assertIn("## 待确认与限制", main)
        self.assertIn("待分析（非无风险）", main)
        self.assertIn("未执行，仅建议", main)
        self.assertNotIn("SEC002", main)
        for evidence in ["SEC002", "vendor/x.py", "unit-1", "selection_plan", "risk_scan"]:
            self.assertIn(evidence, supplement)
        self.assertIn("无命中不代表正确或无风险", supplement)
        self.assertNotIn("完整报告结构见", text)
        self.assertNotIn("Analysis handoff", text)


if __name__ == "__main__":

    unittest.main()
