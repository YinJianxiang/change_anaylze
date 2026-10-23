import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from change_analysis_harness.risk_scan import scan_diff, gate_failed
from change_analysis_harness.harness import main


PATCH = "\n".join([
    "diff --git a/src/demo.py b/src/demo.py", "--- a/src/demo.py", "+++ b/src/demo.py",
    "@@ -1,2 +1,3 @@", " keep", "-old", "+eval(user_input)", "+print('debug')", "",
])


class RiskScanTests(unittest.TestCase):
    def test_new_line_numbers_and_thresholds(self):
        scan = scan_diff(PATCH)
        self.assertEqual([(f["rule_id"], f["line"]) for f in scan["findings"]],
                         [("SEC002", 2), ("DBG001", 3)])
        self.assertTrue(gate_failed(scan, "high"))
        self.assertFalse(gate_failed(scan, None))

    def test_exclusions_match_segments_and_do_not_scan_deleted_lines(self):
        self.assertEqual(scan_diff(PATCH, exclude=("src",))["findings"], [])
        self.assertEqual(len(scan_diff(PATCH, exclude=("sr",))["findings"]), 2)
        deletion = PATCH.replace("-old", "-eval(old_input)").replace("+eval(user_input)", "+safe(user_input)")
        self.assertNotIn("SEC002", [f["rule_id"] for f in scan_diff(deletion)["findings"]])

    def test_truncated_patch_is_rejected(self):
        with self.assertRaises(ValueError):
            scan_diff(PATCH.rsplit("+print", 1)[0])

    def test_metadata_only_and_multiple_hunks(self):
        metadata = "diff --git a/old.png b/new.png\nsimilarity index 100%\nrename from old.png\nrename to new.png\n"
        self.assertEqual(scan_diff(metadata)["scanned_files"], ["new.png"])
        scan = scan_diff(PATCH + "@@ -10 +11 @@\n-old\n+eval(other)\n")
        self.assertEqual(scan["findings"][-1]["line"], 11)
        with self.assertRaises(ValueError):
            scan_diff(PATCH.rsplit("+print", 1)[0] + metadata)

    def test_low_severity_does_not_trigger_high_gate(self):
        scan = scan_diff(PATCH.replace("eval(user_input)", "safe(user_input)"))
        self.assertFalse(gate_failed(scan, "high"))
        self.assertTrue(gate_failed(scan, "low"))

    def test_git_quoted_utf8_and_spaces(self):
        quoted = r'"a/\344\270\255.py" "b/\344\270\255.py"'
        diff = "diff --git " + quoted + '\n@@ -0,0 +1 @@\n+eval(x)\n'
        self.assertEqual(scan_diff(diff)["findings"][0]["file"], "中.py")
        diff = 'diff --git a/a b.py b/a b.py\n@@ -0,0 +1 @@\n+eval(x)\n'
        self.assertEqual(scan_diff(diff)["findings"][0]["file"], "a b.py")

    def test_plain_patch_and_credentials_redacted_from_hints(self):
        scan = scan_diff('--- a/demo.py\n+++ b/demo.py\n@@ -0,0 +1 @@\n+token = "supersecret123"\n')
        self.assertEqual(scan["findings"][0]["line"], 1)
        self.assertNotIn("supersecret123", json.dumps(scan))

    def test_offline_cli_writes_evidence_before_nonzero_gate_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, report = root / "input.patch", root / "out.json", root / "report.md"
            source.write_text(PATCH, encoding="utf-8")
            with patch("change_analysis_harness.harness.GitService.snapshot_repository", side_effect=AssertionError("Git accessed")), \
                 patch("change_analysis_harness.harness.RequirementService.prepare", side_effect=AssertionError("Network accessed")):
                args = ["--diff-file", str(source), "--output", str(output), "--report-output", str(report),
                        "--no-archify", "--env-file", str(root / "missing.env"), "--max-diff-bytes", "10"]
                self.assertEqual(main(args + ["--fail-on", "high"]), 1)
                self.assertEqual(main(args), 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["repository"]["diff"], PATCH)
            self.assertTrue(data["analysis_input"]["repositories"][0]["diff_truncated_for_analysis"])
            self.assertEqual(data["repository"]["risk_scan"]["summary"]["high"], 1)
            self.assertIn("SEC002", report.read_text(encoding="utf-8"))
            self.assertIsNone(data["analysis"])


if __name__ == "__main__":
    unittest.main()
