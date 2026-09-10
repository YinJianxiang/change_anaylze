from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest import mock

from orchestrator.services.requirement_fetchers.url_router import (
    extract_dingtalk_urls,
    parse_requirement_url,
)
from orchestrator.services.requirement_service import RequirementService


class UrlRouterTests(unittest.TestCase):
    def test_parse_tapd_storywall_dialog_preview(self) -> None:
        url = (
            "https://www.tapd.cn/tapd_fe/66882899/storywall"
            "?dialog_preview_id=story_1166882899001024625"
        )
        parsed = parse_requirement_url(url)
        self.assertEqual(parsed.kind, "tapd_story")
        self.assertEqual(parsed.workspace_id, "66882899")
        self.assertEqual(parsed.story_id, "1166882899001024625")

    def test_parse_tapd_iteration_card_dialog(self) -> None:
        url = (
            "https://www.tapd.cn/tapd_fe/66882899/iteration/card/1166882899001001585"
            "?dialog_preview_id=story_1166882899001024586"
        )
        parsed = parse_requirement_url(url)
        self.assertEqual(parsed.kind, "tapd_story")
        self.assertEqual(parsed.story_id, "1166882899001024586")

    def test_parse_dingtalk_url(self) -> None:
        url = "https://alidocs.dingtalk.com/i/nodes/KGZLxjv9VGd4AOgnIZA3KlMLJ6EDybno?utm_scene=team_space"
        parsed = parse_requirement_url(url)
        self.assertEqual(parsed.kind, "dingtalk_doc")
        self.assertEqual(parsed.node_id, "KGZLxjv9VGd4AOgnIZA3KlMLJ6EDybno")

    def test_extract_dingtalk_urls_from_html(self) -> None:
        html = (
            '<p><a href="https://alidocs.dingtalk.com/i/nodes/ABC123xyz">'
            "doc</a></p>"
        )
        self.assertEqual(
            extract_dingtalk_urls(html),
            ["https://alidocs.dingtalk.com/i/nodes/ABC123xyz"],
        )


class RequirementServiceTests(unittest.TestCase):
    def test_disabled_fetch_preserves_not_fetched(self) -> None:
        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "false"}, clear=False):
            result = RequirementService().prepare(["https://docs.example.test/1"])
        self.assertEqual(result["documents"][0]["status"], "NOT_FETCHED")
        self.assertIsNone(result["documents"][0]["content"])

    def test_dedupes_urls(self) -> None:
        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "false"}, clear=False):
            result = RequirementService().prepare(
                ["https://docs.example.test/1", "https://docs.example.test/1", ""]
            )
        self.assertEqual(len(result["documents"]), 1)

    def test_unsupported_url(self) -> None:
        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "true"}, clear=False):
            result = RequirementService().prepare(["https://docs.example.test/1"])
        doc = result["documents"][0]
        self.assertEqual(doc["status"], "UNSUPPORTED")
        self.assertIsNone(doc["content"])

    def test_dingtalk_fetch_success(self) -> None:
        fake = {
            "title": "网赚需求",
            "content": "### 背景\n\n![image.png](https://cdn.example/a.png)",
            "node_id": "NODE1",
            "source": "bridge",
        }
        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "true"}, clear=False):
            with mock.patch(
                "orchestrator.services.requirement_fetchers.pipeline.fetch_dingtalk_document",
                return_value=fake,
            ):
                result = RequirementService().prepare(
                    ["https://alidocs.dingtalk.com/i/nodes/NODE1"]
                )
        doc = result["documents"][0]
        self.assertEqual(doc["status"], "FETCHED")
        self.assertIn("![image.png](https://cdn.example/a.png)", doc["content"])
        self.assertEqual(doc["meta"]["title"], "网赚需求")

    def test_tapd_follows_nested_dingtalk(self) -> None:
        story = {
            "title": "父需求",
            "content": "# 父需求\n\n见钉钉",
            "description_html": (
                '<a href="https://alidocs.dingtalk.com/i/nodes/NESTED1">doc</a>'
            ),
            "workspace_id": "66882899",
            "story_id": "1166882899001024625",
            "story": {},
        }
        nested = {
            "title": "钉钉正文",
            "content": "正文含图 ![x](https://cdn.example/x.png)",
            "node_id": "NESTED1",
            "source": "bridge",
        }
        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "true"}, clear=False):
            with mock.patch(
                "orchestrator.services.requirement_fetchers.pipeline.fetch_tapd_story",
                return_value=story,
            ), mock.patch(
                "orchestrator.services.requirement_fetchers.pipeline.fetch_dingtalk_document",
                return_value=nested,
            ):
                result = RequirementService().prepare(
                    [
                        "https://www.tapd.cn/tapd_fe/66882899/storywall"
                        "?dialog_preview_id=story_1166882899001024625"
                    ]
                )
        doc = result["documents"][0]
        self.assertEqual(doc["status"], "FETCHED")
        self.assertIn("关联钉钉需求文档", doc["content"])
        self.assertIn("https://cdn.example/x.png", doc["content"])

    def test_failure_isolated_per_url(self) -> None:
        def fake_fetch(url: str) -> dict[str, Any]:
            if "bad" in url:
                return {"url": url, "status": "FAILED", "content": None, "meta": {"error": "boom"}}
            return {
                "url": url,
                "status": "FETCHED",
                "content": "ok",
                "meta": {"title": "ok"},
            }

        with mock.patch.dict(os.environ, {"REQUIREMENT_FETCH_ENABLED": "true"}, clear=False):
            with mock.patch(
                "orchestrator.services.requirement_service.fetch_requirement_document",
                side_effect=fake_fetch,
            ):
                result = RequirementService().prepare(
                    [
                        "https://alidocs.dingtalk.com/i/nodes/good",
                        "https://alidocs.dingtalk.com/i/nodes/bad",
                    ]
                )
        statuses = [doc["status"] for doc in result["documents"]]
        self.assertEqual(statuses, ["FETCHED", "FAILED"])


class DingTalkBridgeTests(unittest.TestCase):
    def test_bridge_http_path(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(
                    {"title": "t", "markdown": "hello ![i](https://cdn/i.png)"}
                ).encode("utf-8")

        with mock.patch.dict(
            os.environ,
            {
                "DINGTALK_DOC_BRIDGE_URL": "https://bridge.example/doc",
                "DINGTALK_ACCESS_TOKEN": "",
                "DINGTALK_APP_KEY": "",
            },
            clear=False,
        ):
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
                from orchestrator.services.requirement_fetchers import dingtalk_fetcher

                doc = dingtalk_fetcher.fetch_document(
                    "https://alidocs.dingtalk.com/i/nodes/ABC"
                )
        self.assertEqual(doc["title"], "t")
        self.assertIn("![i](https://cdn/i.png)", doc["content"])


class AgentRequirementModeTests(unittest.TestCase):
    def test_mode_b_when_content_fetched(self) -> None:
        from orchestrator.services.requirement_mode import (
            has_fetched_requirements,
            requirement_mode_instruction,
        )

        payload = {
            "requirements": [
                {
                    "url": "https://alidocs.dingtalk.com/i/nodes/x",
                    "status": "FETCHED",
                    "content": "# req",
                }
            ]
        }
        self.assertTrue(has_fetched_requirements(payload))
        self.assertIn("Mode B", requirement_mode_instruction(payload))

    def test_mode_a_when_not_fetched(self) -> None:
        from orchestrator.services.requirement_mode import requirement_mode_instruction

        payload = {
            "requirements": [
                {"url": "https://docs.example/1", "status": "NOT_FETCHED", "content": None}
            ]
        }
        self.assertIn("Mode A", requirement_mode_instruction(payload))


if __name__ == "__main__":
    unittest.main()
