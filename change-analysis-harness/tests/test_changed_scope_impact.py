from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from change_analysis_harness.evidence.collect_repository_impact import collect_impact
from change_analysis_harness.evidence.java_ast import merge_java_revisions, parse_changed_java_source


class ChangedScopeImpactTests(unittest.TestCase):
    def test_java_ast_keeps_only_changed_scopes_and_calls(self) -> None:
        old_source = """package demo;
public class Demo {
    public int calculate(int value) {
        return helper(value);
    }
    private int helper(int value) {
        return value + 1;
    }
}
"""
        new_source = old_source.replace("return helper(value);", "return helper(value) + 1;")

        old_result = parse_changed_java_source(
            path="src/Demo.java",
            source_text=old_source,
            changed_lines={3},
            revision="base",
        )
        new_result = parse_changed_java_source(
            path="src/Demo.java",
            source_text=new_source,
            changed_lines={3},
            revision="head",
        )
        merged = merge_java_revisions(
            path="src/Demo.java",
            old_result=old_result,
            new_result=new_result,
        )

        scopes = merged["changed_scopes"]
        self.assertTrue(scopes)
        self.assertTrue(all(scope["change_type"] == "modified" for scope in scopes))
        self.assertIn("Demo.calculate", {scope["qualified_name"] for scope in scopes})
        self.assertTrue(all(scope["parser"] == "tree-sitter-java" for scope in scopes))
        self.assertIn("helper", {call["callee_symbol"] for call in merged["calls_from_changed_scopes"]})

    def test_deleted_java_scope_is_classified_as_deleted(self) -> None:
        old_source = """class Removed {
    void oldMethod() {
        call();
    }
}
"""
        old_result = parse_changed_java_source(
            path="src/Removed.java",
            source_text=old_source,
            changed_lines={2, 3, 4},
            revision="base",
        )
        new_result = parse_changed_java_source(
            path="src/Removed.java",
            source_text="",
            changed_lines=set(),
            revision="head",
        )
        merged = merge_java_revisions(
            path="src/Removed.java",
            old_result=old_result,
            new_result=new_result,
        )

        self.assertTrue(any(scope["change_type"] == "deleted" for scope in merged["changed_scopes"]))

    def test_repository_scan_does_not_parse_unchanged_java_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "codex@example.invalid")
            self._git(repo, "config", "user.name", "Codex")
            (repo / "Changed.java").write_text(
                "class Changed {\n    int value() { return 1; }\n}\n", encoding="utf-8"
            )
            (repo / "Unchanged.java").write_text(
                "class Unchanged {\n    int value() { return 2; }\n}\n", encoding="utf-8"
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "base")
            base = self._git(repo, "rev-parse", "HEAD").strip()

            (repo / "Changed.java").write_text(
                "class Changed {\n    int value() { return 3; }\n}\n", encoding="utf-8"
            )
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "change")
            head = self._git(repo, "rev-parse", "HEAD").strip()

            result = collect_impact(repo, f"{base}...{head}", source="range")

            self.assertEqual(result["analysis_mode"], "Changed-scope AST analysis")
            self.assertEqual(result["analysis_scope"], "changed_files_and_changed_ast_scopes_only")
            self.assertFalse(result["whole_repository_analysis"])
            self.assertFalse(result["repository_scan"]["whole_repository_ast_scan"])
            self.assertEqual(result["repository_scan"]["ast_scan_scope"], "changed_files_only")
            self.assertEqual(result["repository_scan"]["reference_search_scope"], "targeted_changed_symbols_only")
            self.assertEqual(result["java_ast"]["files_parsed"], 1)
            self.assertEqual([item["path"] for item in result["java_ast"]["files"]], ["Changed.java"])

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
