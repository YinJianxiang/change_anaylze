from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from change_analysis_harness.report import default_report_path, render_markdown, requirement_directory_name


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

        self.assertIn("# Code Change Analysis Report", text)
        self.assertIn("\u7d20\u6750\u6392\u884c\u699c\u9700\u6c42", text)
        self.assertIn("Demo.calculate", text)
        self.assertIn("Selection and ImpactUnits", text)
        self.assertIn("unit-0-1", text)
        self.assertNotIn("?", text)


if __name__ == "__main__":
    unittest.main()
