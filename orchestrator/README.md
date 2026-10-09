# 机器人运行说明

以下命令均从仓库根目录执行。先运行 `python -m pip install -r requirements.txt`，安装机器人依赖和本地 Harness。

机器人负责邮箱触发、任务队列、模型分析、飞书通知和确认反馈。需求读取、Git 快照和变更证据均复用 Harness；机器人专用指令加载同一份分析指南。分析前会附带 Harness 产出的 `selection_plan` / `impact_units`，优先按 ImpactUnit 分批（`LLM_UNIT_CONCURRENCY` 默认 1），并用 CoverageReflector 把覆盖缺口写入 `uncertainties`。`ANALYSIS_EFFORT=low|medium|high` 控制确认深度。

## Event-driven runtime

Copy `.local/reviewers.example.json` to `.local/reviewers.json` to route mail triggers such as `@张三跟进测试` to different Feishu users. Names are matched exactly and resolved to a stable `open_id` (recommended) or email. If the file is absent, the personal `FEISHU_RECEIVE_ID_TYPE` and `FEISHU_RECEIVE_ID` settings are used for every reviewer name.

Start RabbitMQ, the standalone LLM API, and the event worker:

```powershell
docker compose up --build rabbitmq llm-server task-worker feishu
```

Run one mailbox ingestion after the services are healthy:

```powershell
docker compose --profile ingest run --rm mail-ingest
```

The compose `mail-ingest` service runs as a durable listener and polls the
mailbox every 60 seconds. For a one-shot local scan, omit
`--poll-interval-seconds` (or pass `0`) and run `python -m
orchestrator.mail_ingest` directly. IMAP reads use `BODY.PEEK[]`, and the
SQLite message key makes repeated polls idempotent.

The LLM health endpoint is `http://localhost:8081/healthz`; RabbitMQ management is available at `http://localhost:15672`. Events that were persisted but could not be published can be retried with:

```powershell
python -m orchestrator.outbox_relay --database .local/mail_ingest.sqlite3
```

The mailbox reader reads recent messages from the Aliyun enterprise mailbox without
changing their read state. Messages whose body contains `@xxx跟进测试` are
parsed and printed as JSON. Processed `Message-ID` values are stored in a local
SQLite database to prevent duplicate output.

## Configure

Copy `.env.example` to `.env` and fill in the real values. The command loads
the project-root `.env` automatically. Do not commit real credentials.

```dotenv
ALIYUN_IMAP_USERNAME=account@example.com
ALIYUN_IMAP_PASSWORD=client-specific-password
MAIL_TRIGGER_TEXT=@xxx跟进测试
```

The host defaults to `imap.qiye.aliyun.com`, port `993`, and mailbox `INBOX`.
Values already defined in the process environment take precedence over `.env`.
Use `--env-file path/to/file` to load a different file.
The mailbox scan defaults can also be set in `.env` with `MAIL_SCAN_SINCE_DAYS`, `MAIL_MAX_MESSAGES`, and `MAIL_POLL_INTERVAL_SECONDS`.

## Run once

```powershell
python -m orchestrator.mail_ingest --since-days 7
```

The IMAP connection timeout defaults to 20 seconds. Override it with
`--imap-timeout 30` when needed.
Only the newest 100 messages in the selected date window are downloaded by
default. Override this with `--max-messages 20` for a quick connectivity test.

The reader opens the mailbox as read-only and retrieves messages using
`BODY.PEEK[]`. It does not mark, move, or delete messages.

## Process stored mail

After ingestion, read the saved payloads without reconnecting to IMAP:

```powershell
python -m orchestrator.mail_process --database .local/mail_ingest-test.sqlite3
```

This preview step reads `NEW` records and outputs only the message ID,
project/branch versions, and reference-document URLs. It does not update task
state.

## Run analysis tasks

Copy `.local/projects.example.json` to `.local/projects.json` and configure
each project repository, local path, and base branch. Configure the external
adapters through environment variables:

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5
MAIL_CONTEXT_MAX_CHARS=20000
GIT_USERNAME=oauth2
GIT_PERSONAL_ACCESS_TOKEN=...
REQUIREMENT_FETCH_ENABLED=true
TAPD_ACCESS_TOKEN=...
# DingTalk via MCP Bridge (no OpenAPI AppKey)
DINGTALK_MCP_URL=...   # copy from Cursor mcp.json → 钉钉文档.url
DINGTALK_DOC_BRIDGE_URL=http://127.0.0.1:8091/v1/doc/content
DINGTALK_DOC_BRIDGE_TOKEN=change-me
```

When `REQUIREMENT_FETCH_ENABLED=true`, `RequirementService` fetches mail reference
URLs before LLM analysis: TAPD story pages (and nested DingTalk links in the
description) plus direct `alidocs.dingtalk.com` docs. Markdown keeps images in
place as `![](url)`. Fetched bodies enable Skill Mode B; otherwise Mode A.

### DingTalk without AppKey (MCP Bridge)

There is no DingTalk OpenAPI AppKey path required. Start the local Bridge that
wraps the same Cursor「钉钉文档」MCP gateway:

```powershell
pip install -r dingtalk_doc_bridge\requirements.txt
# set DINGTALK_MCP_URL from %USERPROFILE%\.cursor\mcp.json
python -m dingtalk_doc_bridge --port 8091
```

Details: [`dingtalk_doc_bridge/README.md`](../dingtalk_doc_bridge/README.md).
With Docker: `docker compose --profile dingtalk up -d dingtalk-doc-bridge`
and set `DINGTALK_DOC_BRIDGE_URL=http://dingtalk-doc-bridge:8091/v1/doc/content`.

For direct messages, configure `FEISHU_RECEIVE_ID_TYPE=email` and set
`FEISHU_RECEIVE_ID` to the recipient's enterprise email address. A recipient
can also be supplied for a one-off test with `orchestrator.feishu_bot
--receive-id user@example.com --receive-id-type email`.

For a local interactive bot, enable long-connection event delivery in the
Feishu developer console and subscribe to `im.message.receive_v1`, then run:

```powershell
python -m orchestrator.feishu_long_connection
```

The process receives direct messages without a public callback URL and replies
in the same one-to-one conversation. Analysis messages include a stable task ID.
The reviewer may reply `确认` to accept the analysis, or send correction text.
Corrections are routed back to the Agent together with the previous Skill result;
the revised final conclusion is sent to the same Feishu `open_id`. When a reviewer
has multiple pending tasks, include `任务：<任务ID>` in the reply.

Before the Agent starts, `GitService.snapshot()` fetches the configured base and
target branches and pins `base_commit`, `merge_base`, and `target_commit`. The
OpenAI Agents SDK Agent then calls the `invoke_analyze_change_test_scope` function
tool; only that tool invocation loads bot analysis instructions, the shared Harness
guides, parsed mail context, requirements, and fixed repository evidence. The
Skill is no longer concatenated into the Agent system prompt. Reviewer feedback
is sent through the Agents SDK with the prior response ID when available, so the
final conclusion remains in the same Agent conversation.

For HTTPS repositories, the task runner sends the Personal Access Token as a
per-process HTTP authorization header. It does not modify `remote.origin.url`
or write the token to Git configuration. The token needs read access to every
configured repository.

Run queued tasks:

```powershell
python -m orchestrator.task_worker --database .local/mail_ingest.sqlite3 --limit 10
```

The canonical runtime modules are separated into mailbox ingestion, task
worker orchestration, Git, requirement, LLM, Feishu, storage, and messaging
boundaries. The current SQLite worker remains compatible while RabbitMQ is
introduced as the optional service transport:

```dotenv
RABBITMQ_URL=amqp://user:password@localhost:5672/%2F
```

Use `task_runner` only as a legacy compatibility entry point during migration.

Verify the Agents SDK endpoint without loading the Skill:

```powershell
python agent_sdk_connect.py
```

`agent_sdk_skill_test.py` is a historical Skill-tool experiment, not a supported
bot analysis entry point. Use the task worker for analysis with the shared
Harness evidence and bot-specific execution instructions.

Verify Git access without reading tasks, calling OpenAI, or sending Feishu:

```powershell
python -m orchestrator.task_runner --config .local/projects.json --git-check-project market-admin --git-check-branch dev_xxx
```

The check fetches the latest remote base and target branches without changing
the checked-out working tree. Its output includes the latest base commit, the
fixed target commit, the feature commit list, and changed files.

Retry a failed task:

```powershell
python -m orchestrator.task_runner --database .local/mail_ingest.sqlite3 --retry-message-id "<message-id>"
```

The Feishu webhook service should verify Feishu signatures and pass the
verified card payload to `handle_confirmation`. For local callback testing:

```powershell
python -m orchestrator.feishu_callback --payload '{"message_id":"<message-id>","action":"confirm_success","feedback":"approved"}'
```


## 代码归属

- `mail_ingest.py`、`mail_process.py`：邮箱读取和触发解析。
- `task_worker.py`、`task_runner.py`：事件工作流和本地任务运行；后者保留兼容 CLI。
- `services/git_service.py`：把项目映射转换为 Harness 输入。
- `services/analysis_context.py`、`services/requirement_service.py`、`services/requirement_fetchers/`：旧导入路径的兼容导出，算法在 Harness。
- `services/analysis_instructions.py`：机器人执行约定；共享分析指南来自 Harness 包内资源。
- `services/llm_service.py`、`services/agent_sdk_service.py`：模型调用与结构化报告。
- `feishu_*`、`storage/`、`messaging/`：消息交互、持久化与事件传输。

`python -m orchestrator.change_analysis_harness` 已转发到独立 Harness，只采集证据。需要模型分析和飞书交互时使用机器人任务入口。
