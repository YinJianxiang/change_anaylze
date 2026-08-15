from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.feishu_bot import FeishuError, send_message, tenant_access_token


class FeishuBotTests(unittest.TestCase):
    def test_gets_tenant_token(self) -> None:
        with patch("orchestrator.feishu_bot._post_json", return_value={"code": 0, "tenant_access_token": "token"}) as post:
            self.assertEqual(tenant_access_token("app", "secret"), "token")
        self.assertEqual(post.call_args.args[1], {"app_id": "app", "app_secret": "secret"})

    def test_sends_text_and_returns_message_id(self) -> None:
        responses = [
            {"code": 0, "tenant_access_token": "token"},
            {"code": 0, "data": {"message_id": "om_123"}},
        ]
        with patch("orchestrator.feishu_bot._post_json", side_effect=responses) as post:
            message_id = send_message("app", "secret", "chat_id", "oc_123", "text", {"text": "hello"})
        self.assertEqual(message_id, "om_123")
        send_payload = post.call_args_list[1].args[1]
        self.assertEqual(send_payload["receive_id"], "oc_123")
        self.assertEqual(json.loads(send_payload["content"]), {"text": "hello"})

    def test_missing_message_id_is_an_error(self) -> None:
        with patch("orchestrator.feishu_bot._post_json", side_effect=[{"tenant_access_token": "token"}, {"code": 0, "data": {}}]):
            with self.assertRaises(FeishuError):
                send_message("app", "secret", "chat_id", "oc_123", "text", {"text": "hello"})


if __name__ == "__main__":
    unittest.main()
