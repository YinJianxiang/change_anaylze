from __future__ import annotations

import sys
import unittest
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from change_analysis_harness.coverage_reflector import (
    merge_reflection_into_analysis,
    reflect_analysis_result,
)


class CoverageReflectorTests(unittest.TestCase):
    def test_detects_missing_evidence_refs_and_high_risk(self) -> None:
        result = {
            "result": {
                "summary": "ok",
                "findings": [{"summary": "已确认调用方 FooService"}],
                "risks": [{"summary": "P0 资金风险"}],
                "test_scope": [],
                "uncertainties": [],
            }
        }
        reflection = reflect_analysis_result(result, effort="medium")
        self.assertFalse(reflection["ok"])
        self.assertTrue(any("evidence_refs" in gap or "confirmed" in gap for gap in reflection["gaps"]))
        self.assertIn("high_risk_without_tests", reflection["gaps"])

    def test_merge_appends_uncertainties(self) -> None:
        analysis = {
            "result": {
                "summary": "ok",
                "findings": [],
                "risks": [],
                "test_scope": [],
                "uncertainties": [],
            }
        }
        reflection = {"ok": False, "gaps": ["missing_section:findings"], "effort": "medium", "strict": False}
        merged = merge_reflection_into_analysis(analysis, reflection)
        self.assertTrue(any("CoverageReflector" in item for item in merged["result"]["uncertainties"]))
        self.assertIn("coverage_reflection", merged)


if __name__ == "__main__":
    unittest.main()
