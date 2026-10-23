from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "src"))

from change_analysis_skill.archify_report import (
    build_archify_graph,
    default_archify_path,
    render_archify_html,
)
from change_analysis_skill.report import render_markdown, requirement_directory_name


def _sample_result() -> dict:
    return {
        "request": {
            "requirement_url": "https://example.invalid/story/1",
            "repository": "D:/repo",
            "branch": "feature/demo",
            "base_branch": "master",
        },
        "requirement_documents": [{
            "content": "# demo requirement",
            "meta": {"title": "demo requirement", "story_id": "1166882899001025101"},
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
                "analysis_limits": ["only analyze changed files"],
                "symbol_references": ["Demo.calculate", "Demo.calculate", "Demo.calculate"],
                "database_dependencies": [],
                "frontend_consumers": [],
                "backend_callers": ["DemoService.call"],
                "related_tests": [],
                "repository_scan": {"whole_repository_ast_scan": False},
            },
        },
        "analysis_mode": "Changed-scope AST evidence analysis",
        "evidence_warnings": ["summary diff truncated"],
        "result": {"uncertainties": ["CoverageReflector: missing_section:risks"]},
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
            "related_candidates": ["Demo.calculate"],
        }],
        "status": "READY_FOR_CODEX",
    }


class ArchifyReportTests(unittest.TestCase):

    def test_default_archify_path_is_html_beside_markdown(self) -> None:
        result = _sample_result()
        path = default_archify_path(result, Path("reporter"))
        expected_dir = requirement_directory_name(result)
        self.assertEqual(path, Path("reporter") / expected_dir / "change-analysis-report.html")

    def test_graph_dedupes_candidate_pool(self) -> None:
        graph = build_archify_graph(_sample_result())
        candidate_ids = [n["id"] for n in graph["nodes"] if n["group"] == "candidate"]
        # Three identical "Demo.calculate" references must collapse into one node.
        self.assertEqual(len(candidate_ids), len(set(candidate_ids)))
        self.assertEqual(sum(1 for cid in candidate_ids if cid == "candidate::Demo.calculate"), 1)

    def test_graph_includes_requirement_unit_file_candidate_symbol_uncertainty(self) -> None:
        graph = build_archify_graph(_sample_result())
        groups = {n["id"]: n["group"] for n in graph["nodes"]}
        self.assertIn("requirement", groups.values())
        self.assertIn("unit", groups.values())
        self.assertIn("file", groups.values())
        self.assertIn("candidate", groups.values())
        self.assertIn("symbol", groups.values())
        self.assertIn("uncertainty", groups.values())

    def test_html_is_self_contained_and_marks_unconfirmed(self) -> None:
        html = render_archify_html(_sample_result())
        self.assertIn("<!doctype html>", html)
        self.assertIn("Code Change Impact Graph", html)
        self.assertIn("Demo.calculate", html)
        self.assertIn("CoverageReflector: missing_section:risks", html)
        # Unconfirmed edges are rendered dashed/red in the JSON data.
        self.assertIn('"dashed": true', html)
        # No broken script injection: the embedded JSON data must not contain a raw
        # "</" sequence (we escape it to "<\/" before injecting).
        marker = "const __DATA__ = "
        start = html.index(marker) + len(marker)
        end = html.index(";", start)
        data_segment = html[start:end]
        self.assertNotIn("</", data_segment)

    def test_html_writes_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "impact.html"
            output.write_text(render_archify_html(_sample_result()), encoding="utf-8")
            text = output.read_text(encoding="utf-8")
        self.assertIn("vis-network", text)
        # Ensure the embedded data is valid JSON.
        marker = "const __DATA__ = "
        start = text.index(marker) + len(marker)
        end = text.index(";", start)
        payload = json.loads(text[start:end])
        self.assertIn("graph", payload)
        self.assertIn("meta", payload)

    def test_markdown_includes_inline_graph_and_html_link(self) -> None:
        result = _sample_result()
        result["archify_report"] = {"format": "html", "path": "reporter/x/change-analysis-report.html"}
        md = render_markdown(result)
        self.assertIn("### 影响图（Mermaid）", md)
        self.assertLess(md.index("<details>"), md.index("### 影响图（Mermaid）"))
        self.assertLess(md.index("### 影响图（Mermaid）"), md.index("</details>"))
        self.assertIn("```mermaid", md)
        self.assertIn("change-analysis-report.html", md)
        self.assertIn("Demo.calculate", md)
        # The inline graph and the interactive HTML share the same evidence model.
        self.assertIn("classDef uncertainty", md)


if __name__ == "__main__":
    unittest.main()
