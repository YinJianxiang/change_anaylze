from __future__ import annotations

import argparse
import dataclasses
import email
import hashlib
import html
import imaplib
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from orchestrator.services.reviewer_router import ReviewerRoutingError
from orchestrator.time_utils import now_beijing


DEFAULT_HOST = "imap.qiye.aliyun.com"
DEFAULT_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_TRIGGER = "@xxx跟进测试"
DEFAULT_IMAP_TIMEOUT = 20.0
DEFAULT_MAX_MESSAGES = 100
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class ProjectBranch:
    project: str
    branch: str


@dataclass(frozen=True)
class ParsedRequest:
    message_id: str
    subject: str
    sender: str
    date: str
    trigger_text: str
    projects: list[ProjectBranch]
    requirement_urls: list[str]
    remark: str
    body_text: str
    reviewer_name: str = ""
    receiver_id_type: str = ""
    receiver_id: str = ""
    routing_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {"br", "div", "p", "tr", "td", "th", "li", "table", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts)).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)


def _decode_header(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_body(message: Message) -> tuple[str, list[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    links: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            parser = _HTMLTextExtractor()
            parser.feed(_decode_part(part))
            html_parts.append(parser.text())
            links.extend(parser.links)
    body = "\n".join(html_parts or plain_parts)
    body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    return body, links


def _normalize_for_match(value: str) -> str:
    # Mail clients may insert zero-width formatting characters around mentions.
    return re.sub(r"[\s\u200b\u200c\u200d\u2060\ufeff]+", "", value).casefold()


def parse_reviewer_mentions(value: str, suffix: str = "跟进测试") -> list[str]:
    value = re.sub(r"[\s\u200b\u200c\u200d\u2060\ufeff]+", "", value)
    suffix = re.sub(r"[\s\u200b\u200c\u200d\u2060\ufeff]+", "", suffix)
    pattern = rf"@([A-Za-z0-9_.\-\u4e00-\u9fff]+)\s*{re.escape(suffix)}"
    return list(dict.fromkeys(match.strip() for match in re.findall(pattern, value, flags=re.IGNORECASE)))


def _field_value(body: str, label: str, stop_labels: Iterable[str]) -> str:
    stop = "|".join(re.escape(item) for item in stop_labels)
    pattern = rf"{re.escape(label)}\s*[:：]?\s*(.*?)(?=\n(?:{stop})\s*[:：]?|\Z)"
    match = re.search(pattern, body, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parse_projects(body: str) -> list[ProjectBranch]:
    section_match = re.search(
        r"工程\s*\|\s*分支\s*\|\s*版本号\s*[:：]\s*(.*?)"
        r"(?=\n(?:工程依赖|数据脚本|配置更新|提测接口|参考文档|接口修改|目标环境|测试内容|备注)\s*[:：]|\Z)",
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if section_match:
        projects: list[ProjectBranch] = []
        seen: set[tuple[str, str]] = set()
        for line in section_match.group(1).splitlines():
            match = re.match(r"\s*([\w.-]+)\s*\|\s*(\S+)\s*$", line)
            if match:
                item = ProjectBranch(match.group(1), match.group(2))
                key = (item.project, item.branch)
                if key not in seen:
                    projects.append(item)
                    seen.add(key)
        return projects

    labels = ["工程分支版本号", "工程依赖", "数据脚本", "配置更新", "提测接口", "参考文档", "接口修改", "目标环境", "测试内容", "备注"]
    section = _field_value(body, "工程分支版本号", labels[1:])
    projects: list[ProjectBranch] = []
    seen: set[tuple[str, str]] = set()
    for line in section.splitlines():
        match = re.match(r"\s*([\w.-]+)\s*[|｜]\s*([^\s|｜]+)\s*$", line)
        if not match:
            continue
        item = ProjectBranch(match.group(1), match.group(2))
        key = (item.project, item.branch)
        if key not in seen:
            projects.append(item)
            seen.add(key)
    return projects


def parse_message(
    raw_message: bytes,
    trigger_text: str = DEFAULT_TRIGGER,
    *,
    require_trigger: bool = True,
) -> ParsedRequest | None:
    message = email.message_from_bytes(raw_message)
    body, html_links = extract_body(message)
    if require_trigger and _normalize_for_match(trigger_text) not in _normalize_for_match(body):
        return None
    urls = re.findall(r"https?://[^\s<>\"']+", body)
    requirement_urls = list(dict.fromkeys(url.rstrip(".,;，。；)") for url in [*html_links, *urls]))
    labels = ["工程依赖", "数据脚本", "配置更新", "提测接口", "参考文档", "接口修改", "目标环境", "测试内容", "备注"]
    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = f"missing:{hashlib.sha256(raw_message).hexdigest()}"
    return ParsedRequest(
        message_id=message_id,
        subject=_decode_header(message.get("Subject")),
        sender=_decode_header(message.get("From")),
        date=message.get("Date", ""),
        trigger_text=trigger_text,
        projects=_parse_projects(body),
        requirement_urls=requirement_urls,
        remark=_field_value(body, "备注", labels),
        body_text=body,
    )


class MessageStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS processed_messages "
            "(message_id TEXT PRIMARY KEY, task_id TEXT UNIQUE, processed_at TEXT NOT NULL, payload TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'NEW', retry_count INTEGER NOT NULL DEFAULT 0, "
            "last_error TEXT, updated_at TEXT, completed_at TEXT, result_payload TEXT, "
            "feedback TEXT, robot_message_id TEXT, sender_open_id TEXT, chat_id TEXT, "
            "reviewer_name TEXT, receiver_id_type TEXT, receiver_id TEXT, routing_error TEXT, "
            "created_at TEXT, locked_at TEXT, locked_by TEXT)"
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(processed_messages)")}
        migrations = {
            "task_id": "ALTER TABLE processed_messages ADD COLUMN task_id TEXT",
            "status": "ALTER TABLE processed_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'NEW'",
            "retry_count": "ALTER TABLE processed_messages ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
            "last_error": "ALTER TABLE processed_messages ADD COLUMN last_error TEXT",
            "updated_at": "ALTER TABLE processed_messages ADD COLUMN updated_at TEXT",
            "completed_at": "ALTER TABLE processed_messages ADD COLUMN completed_at TEXT",
            "result_payload": "ALTER TABLE processed_messages ADD COLUMN result_payload TEXT",
            "feedback": "ALTER TABLE processed_messages ADD COLUMN feedback TEXT",
            "robot_message_id": "ALTER TABLE processed_messages ADD COLUMN robot_message_id TEXT",
            "sender_open_id": "ALTER TABLE processed_messages ADD COLUMN sender_open_id TEXT",
            "chat_id": "ALTER TABLE processed_messages ADD COLUMN chat_id TEXT",
            "reviewer_name": "ALTER TABLE processed_messages ADD COLUMN reviewer_name TEXT",
            "receiver_id_type": "ALTER TABLE processed_messages ADD COLUMN receiver_id_type TEXT",
            "receiver_id": "ALTER TABLE processed_messages ADD COLUMN receiver_id TEXT",
            "routing_error": "ALTER TABLE processed_messages ADD COLUMN routing_error TEXT",
            "created_at": "ALTER TABLE processed_messages ADD COLUMN created_at TEXT",
            "locked_at": "ALTER TABLE processed_messages ADD COLUMN locked_at TEXT",
            "locked_by": "ALTER TABLE processed_messages ADD COLUMN locked_by TEXT",
        }
        for name, statement in migrations.items():
            if name not in columns:
                self.connection.execute(statement)
        self.connection.execute("UPDATE processed_messages SET task_id = message_id WHERE task_id IS NULL")
        self.connection.execute("UPDATE processed_messages SET created_at = processed_at WHERE created_at IS NULL")
        self.connection.commit()

    def contains(self, message_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def save(self, request: ParsedRequest) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO processed_messages"
            "(message_id, task_id, processed_at, payload, status, retry_count, updated_at, created_at,"
            "reviewer_name,receiver_id_type,receiver_id,routing_error) "
            "VALUES (?, ?, ?, ?, 'NEW', 0, ?, ?, ?, ?, ?, ?)",
            (
                request.message_id,
                request.message_id,
                now_beijing(),
                json.dumps(request.to_dict(), ensure_ascii=False),
                now_beijing(),
                now_beijing(),
                request.reviewer_name,
                request.receiver_id_type,
                request.receiver_id,
                request.routing_error,
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class AliyunImapReader:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        mailbox: str = DEFAULT_MAILBOX,
        timeout: float = DEFAULT_IMAP_TIMEOUT,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.timeout = timeout
        self.max_messages = max_messages

    def fetch_since(self, since_days: int) -> list[bytes]:
        since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        try:
            print(f"Connecting to IMAP {self.host}:{self.port}...", file=sys.stderr, flush=True)
            client = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        except (OSError, imaplib.IMAP4.error) as error:
            raise RuntimeError(f"IMAP connection failed for {self.host}:{self.port}: {error}") from error
        try:
            try:
                client.login(self.username, self.password)
            except (OSError, imaplib.IMAP4.error) as error:
                raise RuntimeError(f"IMAP login failed for {self.username}: {error}") from error
            status, _ = client.select(self.mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select mailbox: {self.mailbox}")
            status, data = client.uid("search", None, "SINCE", since)
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            messages: list[bytes] = []
            all_uids = (data[0] or b"").split()
            selected_uids = all_uids[-self.max_messages :] if self.max_messages else all_uids
            print(
                f"Found {len(all_uids)} messages since {since}; reading the newest {len(selected_uids)}.",
                file=sys.stderr,
                flush=True,
            )
            for index, uid in enumerate(selected_uids, start=1):
                status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
                if raw:
                    messages.append(raw)
                if index == 1 or index % 10 == 0 or index == len(selected_uids):
                    print(f"Read {index}/{len(selected_uids)} messages.", file=sys.stderr, flush=True)
            return messages
        finally:
            try:
                client.logout()
            except imaplib.IMAP4.error:
                pass


def _message_id(value: str | None) -> str:
    return (value or "").strip()


def _replied_to_message_id(message: Message) -> str:
    in_reply_to = re.findall(r"<[^<>]+>", message.get("In-Reply-To", ""))
    if in_reply_to:
        return in_reply_to[-1]
    references = re.findall(r"<[^<>]+>", message.get("References", ""))
    return references[-1] if references else ""


def run_once(
    reader: AliyunImapReader,
    store: MessageStore,
    trigger_text: str,
    since_days: int,
    publisher=None,
    outbox=None,
    reviewer_router=None,
) -> list[ParsedRequest]:
    messages_by_id: dict[str, bytes] = {}
    replied_to_ids: list[tuple[str, bytes, list[str]]] = []
    for raw in reader.fetch_since(since_days):
        message = email.message_from_bytes(raw)
        message_id = _message_id(message.get("Message-ID"))
        if message_id:
            messages_by_id[message_id] = raw
        body, _ = extract_body(message)
        mentions = parse_reviewer_mentions(body, os.environ.get("MAIL_TRIGGER_SUFFIX", "跟进测试"))
        if _normalize_for_match(trigger_text) in _normalize_for_match(body) or mentions:
            replied_to_id = _replied_to_message_id(message)
            if replied_to_id:
                replied_to_ids.append((replied_to_id, raw, mentions))

    results: list[ParsedRequest] = []
    seen_ids: set[str] = set()
    for replied_to_id, trigger_raw, mentions in replied_to_ids:
        if replied_to_id in seen_ids:
            continue
        seen_ids.add(replied_to_id)
        raw = messages_by_id.get(replied_to_id)
        if raw is None:
            raw = trigger_raw
        request = parse_message(raw, trigger_text, require_trigger=False)
        if request is None or store.contains(request.message_id):
            continue
        if len(mentions) > 1:
            request = dataclasses.replace(request, routing_error="Multiple reviewers were specified")
        elif mentions:
            if reviewer_router is None:
                request = dataclasses.replace(request, reviewer_name=mentions[0])
            else:
                try:
                    route = reviewer_router.resolve(mentions[0])
                    request = dataclasses.replace(request, reviewer_name=route.name,
                                                  receiver_id_type=route.receive_id_type,
                                                  receiver_id=route.receive_id)
                except ReviewerRoutingError:
                    continue
        store.save(request)
        if publisher is not None:
            from orchestrator.messaging.events import make_event
            event = make_event(request.message_id, "mail.task.created", dataclasses.asdict(request))
            if outbox is not None:
                outbox.enqueue(event)
            publisher("mail.task.created", event)
            if outbox is not None:
                outbox.mark_published(event["event_id"])
        results.append(request)
    return results


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid environment variable name at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and parse matching Aliyun enterprise mailbox messages.")
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--imap-timeout", type=float, default=DEFAULT_IMAP_TIMEOUT)
    parser.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES)
    parser.add_argument("--reviewers", type=Path, default=Path(".local/reviewers.json"))
    args = parser.parse_args()
    load_env_file(args.env_file)
    reader = AliyunImapReader(
        os.environ.get("ALIYUN_IMAP_HOST", DEFAULT_HOST),
        int(os.environ.get("ALIYUN_IMAP_PORT", str(DEFAULT_PORT))),
        _required_env("ALIYUN_IMAP_USERNAME"),
        _required_env("ALIYUN_IMAP_PASSWORD"),
        os.environ.get("ALIYUN_IMAP_MAILBOX", DEFAULT_MAILBOX),
        args.imap_timeout,
        args.max_messages,
    )
    store = MessageStore(args.database)
    from orchestrator.messaging.rabbitmq import RabbitMQ
    from orchestrator.storage.outbox_store import OutboxStore
    from orchestrator.services.reviewer_router import ReviewerRouter
    broker = None
    outbox = OutboxStore(args.database)
    try:
        broker = RabbitMQ()
        trigger_text = os.environ.get("MAIL_TRIGGER_TEXT", DEFAULT_TRIGGER)
        router = ReviewerRouter.from_file(args.reviewers)
        requests = run_once(reader, store, trigger_text, args.since_days, broker.publish, outbox, router)
    finally:
        if broker is not None:
            broker.close()
        outbox.close()
        store.close()
    print(json.dumps([item.body_text for item in requests], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
