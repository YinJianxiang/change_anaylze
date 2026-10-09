from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from change_analysis_harness.selection import (
    build_analysis_planning,
    build_impact_units,
    build_selection_plan,
    format_preview,
)


def _snapshot() -> dict:
    return {
        "project": "demo",
        "diff": (
            "diff --git a/src/main/java/com/demo/api/FooController.java b/src/main/java/com/demo/api/FooController.java\n"
            "+class FooController {}\n"
            "diff --git a/src/main/java/com/demo/api/FooService.java b/src/main/java/com/demo/api/FooService.java\n"
            "+class FooService {}\n"
            "diff --git a/src/main/resources/application.yml b/src/main/resources/application.yml\n"
            "+foo: 1\n"
        ),
        "changed_files": [
            "src/main/java/com/demo/api/FooController.java",
            "src/main/java/com/demo/api/FooService.java",
            "src/main/resources/application.yml",
        ],
        "change_context": {
            "changed_files": [
                {"path": "src/main/java/com/demo/api/FooController.java", "change_type": "modified"},
                {"path": "src/main/java/com/demo/api/FooService.java", "change_type": "modified"},
                {"path": "src/main/resources/application.yml", "change_type": "modified"},
            ]
        },
        "repository_impact": {
            "analysis_limits": ["changed scope only"],
            "java_ast": {
                "files": [
                    {
                        "path": "src/main/java/com/demo/api/FooController.java",
                        "changed_scopes": [{"qualified_name": "FooController.handle", "kind": "method"}],
                    },
                    {
                        "path": "src/main/java/com/demo/api/FooService.java",
                        "changed_scopes": [{"qualified_name": "FooService.run", "kind": "method"}],
                    },
                ]
            },
            "symbol_references": [
                {"path": "src/test/java/FooControllerTest.java", "symbol": "FooController"},
            ],
        },
        "evidence_warnings": [],
        "diff_truncated_for_analysis": False,
    }


class SelectionTests(unittest.TestCase):
    def test_selection_plan_marks_non_java_light(self) -> None:
        plan = build_selection_plan(_snapshot())
        by_path = {item["path"]: item for item in plan["files"]}
        self.assertEqual(by_path["src/main/resources/application.yml"]["status"], "non_java_light")
        self.assertEqual(by_path["src/main/java/com/demo/api/FooController.java"]["status"], "included")
        self.assertEqual(by_path["src/main/java/com/demo/api/FooController.java"]["ast_scope_count"], 1)

    def test_impact_units_group_related_files(self) -> None:
        units = build_impact_units(_snapshot())
        self.assertTrue(units)
        controller_unit = next(
            unit for unit in units
            if any(path.endswith("FooController.java") or path.endswith("FooService.java") for path in unit["files"])
        )
        self.assertTrue(any(path.endswith("FooController.java") for path in controller_unit["files"]))
        self.assertTrue(any(path.endswith("FooService.java") for path in controller_unit["files"]))
        self.assertIn("api", controller_unit["applicable_guide_sections"])
        self.assertIn("evidence-priority", controller_unit["applicable_guide_sections"])
        self.assertGreater(controller_unit["diff_byte_estimate"], 0)

    def test_planning_and_preview(self) -> None:
        planning = build_analysis_planning([_snapshot()], effort="high")
        self.assertEqual(planning["effort"], "high")
        self.assertTrue(planning["impact_units"])
        text = format_preview(planning)
        self.assertIn("selection_plan:", text)
        self.assertIn("impact_units:", text)


if __name__ == "__main__":
    unittest.main()
