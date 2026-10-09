from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


HARNESS_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = HARNESS_ROOT / "tests" / "fixtures" / "MaterialPlineMediaBookDay.java"


class TreeSitterCompatibilityTests(unittest.TestCase):
    def test_complex_java_fixture_is_parsed_without_native_crash(self) -> None:
        script = r'''
import json
import pathlib
import sys

from change_analysis_harness.evidence.java_ast import parse_changed_java_source

fixture = pathlib.Path(sys.argv[1])
source = fixture.read_text(encoding="utf-8")
result = parse_changed_java_source(
    path=str(fixture),
    source_text=source,
    changed_lines=set(range(1, len(source.splitlines()) + 1)),
    revision="head",
)
print(json.dumps({
    "scope_names": [scope["qualified_name"] for scope in result["scopes"]],
    "annotations": result["scopes"][0]["annotations"],
    "parser": result["scopes"][0]["parser"],
}, ensure_ascii=False))
'''
        env = os.environ.copy()
        source_root = str(HARNESS_ROOT / "src")
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(FIXTURE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(
                "复杂 Java fixture 解析失败或触发原生崩溃。\n"
                f"returncode={completed.returncode}\n"
                f"stdout={completed.stdout}\n"
                f"stderr={completed.stderr}"
            ),
        )
        result = json.loads(completed.stdout)
        self.assertIn("MaterialPlineMediaBookDay", result["scope_names"])
        self.assertIn("MaterialPlineMediaBookDay.pkVal", result["scope_names"])
        self.assertIn('@TableName("ad_material_pline_media_book_day")', result["annotations"])
        self.assertEqual(result["parser"], "tree-sitter-java")


if __name__ == "__main__":
    unittest.main()
