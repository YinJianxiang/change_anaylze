from __future__ import annotations

import unittest

from orchestrator.services.requirement_fetchers.markdown_normalize import (
    html_fragment_to_markdown,
    normalize_requirement_markdown,
)


class MarkdownNormalizeTests(unittest.TestCase):
    def test_html_list_becomes_markdown_list(self) -> None:
        html = "背景：x<br><ul><li>1. 新增业务线</li><br><li>2. 媒体增加快手</li></ul>"
        text = html_fragment_to_markdown(html)
        self.assertIn("背景：x", text)
        self.assertIn("1. 新增业务线", text)
        self.assertIn("2. 媒体增加快手", text)
        self.assertNotIn("<li>", text)
        self.assertNotIn("<ul>", text)
        self.assertIn("1. 新增业务线\n2. 媒体增加快手", text)

    def test_nested_margin_left_is_preserved(self) -> None:
        html = (
            "<ul>"
            "<li>1. 筛选项</li>"
            '<li style="margin-left:1em">1. 短剧id</li>'
            '<li style="margin-left:1em">2. 短剧名称</li>'
            "<li>2. 列表字段</li>"
            '<li style="margin-left:2em">1. 上架条件</li>'
            "</ul>"
        )
        text = html_fragment_to_markdown(html)
        self.assertIn("1. 筛选项\n  1. 短剧id\n  2. 短剧名称\n2. 列表字段\n    1. 上架条件", text)

    def test_span_styles_are_stripped_but_text_kept(self) -> None:
        html = '<span style="background-color: #F9DDB2;">传剧账户id</span>'
        self.assertEqual(html_fragment_to_markdown(html), "传剧账户id")

    def test_prototype_table_keeps_grid_and_cleans_html(self) -> None:
        markdown = """
#### 标题

| 需求原型 | 描述 |
|------------|------|
| ![image.png](https://cdn.example/a.png) | **添加**<br><ul><li>1. 快手账户必填</li><br><li style="margin-left:1em">1. 子项</li></ul> |
""".strip()
        result = normalize_requirement_markdown(markdown)
        self.assertIn("#### 标题", result)
        self.assertIn("| 需求原型 | 描述 |", result)
        self.assertIn("| --- | --- |", result)
        self.assertIn("![image.png](https://cdn.example/a.png)", result)
        self.assertIn("1. 快手账户必填", result)
        self.assertIn("1. 子项", result)
        self.assertIn("<br>", result)
        self.assertNotIn("<ul>", result)
        self.assertNotIn("<li>", result)
        self.assertNotIn("条目 1", result)

    def test_multi_row_table_keeps_grid(self) -> None:
        markdown = """
| 需求图 | 描述 |
|---------|------|
| ![a.png](https://cdn.example/a.png) | <ul><li>1. A</li></ul> |
| ![b.png](https://cdn.example/b.png) | <ul><li>1. B</li></ul> |
""".strip()
        result = normalize_requirement_markdown(markdown)
        rows = [line for line in result.splitlines() if line.startswith("|")]
        self.assertGreaterEqual(len(rows), 4)  # header + sep + 2 data
        self.assertIn("1. A", result)
        self.assertIn("1. B", result)
        self.assertNotIn("**条目 1**", result)

    def test_plain_markdown_unchanged(self) -> None:
        markdown = "# 标题\n\n1. 一条\n2. 两条\n"
        self.assertEqual(normalize_requirement_markdown(markdown), markdown)


if __name__ == "__main__":
    unittest.main()
