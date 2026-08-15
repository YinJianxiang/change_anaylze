from __future__ import annotations

import sys
import os
import unittest
import uuid
import tempfile
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.mail_ingest import DEFAULT_TRIGGER, MessageStore, load_env_file, parse_message, parse_reviewer_mentions, run_once  # noqa: E402
from orchestrator.services.reviewer_router import ReviewerRouter  # noqa: E402


def make_message(
    body: str,
    subtype: str = "html",
    message_id: str = "<mail-1@example.test>",
    in_reply_to: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["Subject"] = "提测：广告管控增加字段"
    message["From"] = "requester@example.test"
    message["To"] = "qa@example.test"
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body, subtype=subtype, charset="utf-8")
    return message.as_bytes()


HTML_BODY = """
<html><body>
<p>请 @xxx 跟进测试</p>
<table>
  <tr><td>工程分支版本号：</td><td>market-admin | dev_20260811_jgt_datacontrol45<br>
  market-job | dev_20260811_jgt_datacontrol45</td></tr>
  <tr><td>工程依赖：</td><td></td></tr>
  <tr><td>参考文档：</td><td><a href="https://alidocs.dingtalk.com/i/nodes/example">需求文档</a></td></tr>
  <tr><td>备注：</td><td>本次提测需求的4,5部分<br>检查全域ROI_H24</td></tr>
</table>
</body></html>
"""


class MailParserTests(unittest.TestCase):
    def test_extracts_reviewer_name(self) -> None:
        self.assertEqual(parse_reviewer_mentions("请 @张三 跟进测试"), ["张三"])

    def test_extracts_reviewer_after_removing_format_characters(self) -> None:
        self.assertEqual(parse_reviewer_mentions("@殷健翔\u200d 跟\n进\u2060测试"), ["殷健翔"])

    def test_extracts_distinct_multiple_reviewers(self) -> None:
        self.assertEqual(parse_reviewer_mentions("@张三跟进测试 @李四跟进测试"), ["张三", "李四"])

    def test_parses_matching_html_table(self) -> None:
        request = parse_message(make_message(HTML_BODY))
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual([(item.project, item.branch) for item in request.projects], [
            ("market-admin", "dev_20260811_jgt_datacontrol45"),
            ("market-job", "dev_20260811_jgt_datacontrol45"),
        ])
        self.assertEqual(request.requirement_urls, ["https://alidocs.dingtalk.com/i/nodes/example"])
        self.assertIn("全域ROI_H24", request.remark)

    def test_trigger_matching_ignores_whitespace(self) -> None:
        request = parse_message(make_message("请 @xxx   跟进测试\n工程分支版本号：\napi | feature-1", "plain"))
        self.assertIsNotNone(request)

    def test_trigger_matching_ignores_zero_width_characters(self) -> None:
        request = parse_message(make_message("@殷健翔\u200d跟进测试", "plain"), "@殷健翔跟进测试")
        self.assertIsNotNone(request)

    def test_ignores_non_matching_message(self) -> None:
        self.assertIsNone(parse_message(make_message("普通邮件", "plain")))


class FakeReader:
    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages

    def fetch_since(self, since_days: int) -> list[bytes]:
        return self.messages


class MailStoreTests(unittest.TestCase):
    def test_trigger_reviewer_is_fixed_on_original_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            store = MessageStore(database)
            try:
                original_id = "<original@example.test>"
                original = make_message("Project branch version:\napi | feature-1", "plain", original_id)
                trigger = make_message("@张三跟进测试", "plain", "<reply@example.test>", original_id)
                router = ReviewerRouter({"张三": {"open_id": "ou_123"}})
                result = run_once(FakeReader([original, trigger]), store, DEFAULT_TRIGGER, 7, reviewer_router=router)
                self.assertEqual((result[0].reviewer_name, result[0].receiver_id_type, result[0].receiver_id),
                                 ("张三", "open_id", "ou_123"))
            finally:
                store.close()

    def test_unconfigured_reviewer_is_filtered_before_storage_and_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            store = MessageStore(database)
            try:
                original_id = "<original@example.test>"
                original = make_message("Project branch version:\napi | feature-1", "plain", original_id)
                trigger = make_message("@李四跟进测试", "plain", "<reply@example.test>", original_id)
                router = ReviewerRouter({"张三": {"open_id": "ou_123"}})
                published = []

                result = run_once(
                    FakeReader([original, trigger]),
                    store,
                    DEFAULT_TRIGGER,
                    7,
                    publisher=lambda *args: published.append(args),
                    reviewer_router=router,
                )

                self.assertEqual(result, [])
                self.assertFalse(store.contains(original_id))
                self.assertEqual(published, [])
            finally:
                store.close()

    def test_message_replied_to_by_trigger_is_processed_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            store = MessageStore(database)
            try:
                trigger_id = "<trigger@example.test>"
                original = make_message(
                "Project branch version:\napi | feature-1",
                "plain",
                trigger_id,
            )
                trigger = make_message(
                "@xxx跟进测试",
                "plain",
                "<reply@example.test>",
                trigger_id,
            )
                reader = FakeReader([original, trigger])
                self.assertEqual(len(run_once(reader, store, DEFAULT_TRIGGER, 7)), 1)
                self.assertEqual(len(run_once(reader, store, DEFAULT_TRIGGER, 7)), 0)
            finally:
                store.close()

    def test_trigger_message_without_reply_is_not_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mail.sqlite3"
            store = MessageStore(database)
            try:
                reader = FakeReader([make_message(HTML_BODY)])
                self.assertEqual(run_once(reader, store, DEFAULT_TRIGGER, 7), [])
            finally:
                store.close()


class EnvFileTests(unittest.TestCase):
    def test_loads_env_without_overwriting_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "test.env"
            env_file.write_text(
            "# mailbox config\n"
            "TEST_MAIL_USERNAME=mail@example.test\n"
            "export TEST_MAIL_PASSWORD='secret value'\n"
            "TEST_MAIL_EXISTING=from-file\n",
            encoding="utf-8",
        )
            os.environ["TEST_MAIL_EXISTING"] = "from-process"
            try:
                load_env_file(env_file)
                self.assertEqual(os.environ["TEST_MAIL_USERNAME"], "mail@example.test")
                self.assertEqual(os.environ["TEST_MAIL_PASSWORD"], "secret value")
                self.assertEqual(os.environ["TEST_MAIL_EXISTING"], "from-process")
            finally:
                for name in ("TEST_MAIL_USERNAME", "TEST_MAIL_PASSWORD", "TEST_MAIL_EXISTING"):
                    os.environ.pop(name, None)


if __name__ == "__main__":
    unittest.main()
