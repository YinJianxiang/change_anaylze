"""Run a local Feishu bot through the official long-connection client."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, P2ImMessageReceiveV1

from orchestrator.mail_ingest import load_env_file
from orchestrator.time_utils import now_beijing


class MessageDeduplicator:
    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS feishu_messages "
            "(message_id TEXT PRIMARY KEY, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self.connection.commit()

    def claim(self, message_id: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO feishu_messages(message_id,received_at) VALUES (?,?)", (message_id, now_beijing())
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()


def reply_text(client: lark.Client, chat_id: str, text: str) -> None:
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        raise RuntimeError(f"Feishu reply failed: code={response.code}, msg={response.msg}")


def build_message_handler(client: lark.Client, deduplicator: MessageDeduplicator):
    def handle(data: P2ImMessageReceiveV1) -> None:
        event = data.event
        message = event.message
        if message.chat_type != "p2p" or not deduplicator.claim(message.message_id):
            return
        try:
            content = json.loads(message.content or "{}")
            text = content.get("text", "") if isinstance(content, dict) else ""
        except json.JSONDecodeError:
            text = ""
        if text.strip():
            reply_text(client, message.chat_id, f"已收到：{text.strip()}")
        else:
            reply_text(client, message.chat_id, "已收到消息，目前仅支持文本。")
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Feishu long-connection bot.")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--database", type=Path, default=Path(".local/feishu_bot.sqlite3"))
    args = parser.parse_args()
    load_env_file(args.env_file)
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        parser.error("FEISHU_APP_ID and FEISHU_APP_SECRET are required")
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    deduplicator = MessageDeduplicator(args.database)
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(build_message_handler(client, deduplicator))
        .build()
    )
    ws_client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    print("Feishu bot connected. Waiting for direct messages...", flush=True)
    ws_client.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
