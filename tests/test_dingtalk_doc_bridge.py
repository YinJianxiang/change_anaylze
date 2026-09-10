from __future__ import annotations

import unittest
from types import SimpleNamespace

from dingtalk_doc_bridge.__main__ import _parse_tool_payload


class ParseToolPayloadTests(unittest.TestCase):
    def test_structured_content(self) -> None:
        result = SimpleNamespace(
            structuredContent={"title": "需求", "markdown": "### a\n![i](u)"},
            content=[],
        )
        parsed = _parse_tool_payload(result)
        self.assertEqual(parsed["title"], "需求")
        self.assertIn("![i](u)", parsed["markdown"])

    def test_json_text_content(self) -> None:
        result = SimpleNamespace(
            structuredContent=None,
            content=[SimpleNamespace(text='{"title":"T","markdown":"body"}')],
        )
        parsed = _parse_tool_payload(result)
        self.assertEqual(parsed["title"], "T")
        self.assertEqual(parsed["markdown"], "body")


if __name__ == "__main__":
    unittest.main()
