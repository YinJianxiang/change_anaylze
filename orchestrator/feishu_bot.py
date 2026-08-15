"""Minimal Feishu application bot client for sending chat messages."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from orchestrator.mail_ingest import load_env_file


class FeishuError(RuntimeError):
    pass


def _post_json(url: str, payload: dict[str, object], token: str | None = None) -> dict[str, object]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise FeishuError(f"Feishu HTTP {error.code}: {detail}") from error
    if not isinstance(value, dict):
        raise FeishuError("Feishu returned a non-object response")
    if value.get("code", 0) != 0:
        raise FeishuError(f"Feishu API error {value.get('code')}: {value.get('msg', 'unknown error')}")
    return value


def tenant_access_token(app_id: str, app_secret: str) -> str:
    value = _post_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    token = value.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise FeishuError("Feishu token response is missing tenant_access_token")
    return token


def send_message(
    app_id: str,
    app_secret: str,
    receive_id_type: str,
    receive_id: str,
    msg_type: str,
    content: dict[str, object],
) -> str:
    token = tenant_access_token(app_id, app_secret)
    query = urllib.parse.urlencode({"receive_id_type": receive_id_type})
    value = _post_json(
        f"https://open.feishu.cn/open-apis/im/v1/messages?{query}",
        {"receive_id": receive_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
        token,
    )
    data = value.get("data", {})
    message_id = data.get("message_id") if isinstance(data, dict) else None
    if not isinstance(message_id, str) or not message_id:
        raise FeishuError("Feishu send response is missing data.message_id")
    return message_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a message through a Feishu application bot.")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--text", default="代码变更分析机器人已上线")
    parser.add_argument("--receive-id", help="Recipient email/open_id/user_id; overrides FEISHU_RECEIVE_ID")
    parser.add_argument("--receive-id-type", choices=("email", "open_id", "user_id", "union_id", "chat_id"))
    args = parser.parse_args()
    load_env_file(args.env_file)
    import os
    required = {
        "FEISHU_APP_ID": os.environ.get("FEISHU_APP_ID", "").strip(),
        "FEISHU_APP_SECRET": os.environ.get("FEISHU_APP_SECRET", "").strip(),
        "FEISHU_RECEIVE_ID": (args.receive_id or os.environ.get("FEISHU_RECEIVE_ID", "")).strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("missing environment variables: " + ", ".join(missing))
    receive_id_type = args.receive_id_type or os.environ.get("FEISHU_RECEIVE_ID_TYPE", "email").strip()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    message_id = send_message(
        required["FEISHU_APP_ID"], required["FEISHU_APP_SECRET"],
        receive_id_type, required["FEISHU_RECEIVE_ID"], "text", {"text": args.text},
    )
    print(json.dumps({"sent": True, "message_id": message_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
