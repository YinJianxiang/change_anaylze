from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from change_analysis_harness.guide_match import applicable_guide_sections, match_section_ids
from change_analysis_harness.guidance import load_guide_sections


class GuideMatchTests(unittest.TestCase):
    def test_controller_matches_api(self) -> None:
        self.assertEqual(match_section_ids("src/main/java/com/demo/FooController.java"), ["api"])

    def test_mapper_xml_matches_sql(self) -> None:
        self.assertIn("sql", match_section_ids("src/main/resources/mapper/FooMapper.xml"))
        self.assertNotIn("api", match_section_ids("src/main/resources/mapper/FooMapper.xml"))

    def test_applicable_sections_keep_common_first(self) -> None:
        sections = applicable_guide_sections([
            "src/main/java/com/demo/FooController.java",
            "src/main/resources/application.yml",
        ])
        self.assertEqual(sections[0], "evidence-priority")
        self.assertIn("api", sections)
        self.assertIn("config", sections)
        self.assertIn("test-design", sections)

    def test_load_guide_sections_filters(self) -> None:
        text = load_guide_sections(["api", "sql"])
        self.assertIn("Guide section: api", text)
        self.assertIn("Guide section: sql", text)
        self.assertNotIn("Guide section: jobs", text)


if __name__ == "__main__":
    unittest.main()
