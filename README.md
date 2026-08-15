# Change Analysis

## Event-driven runtime

Copy `.local/reviewers.example.json` to `.local/reviewers.json` to route mail triggers such as `@张三跟进测试` to different Feishu users. Names are matched exactly and resolved to a stable `open_id` (recommended) or email. If the file is absent, the personal `FEISHU_RECEIVE_ID_TYPE` and `FEISHU_RECEIVE_ID` settings are used for every reviewer name.

Start RabbitMQ, the standalone LLM API, and the event worker:

```powershell
docker compose up --build rabbitmq llm-server task-worker
```

Run one mailbox ingestion after the services are healthy:

```powershell
docker compose --profile ingest run --rm mail-ingest
```

The LLM health endpoint is `http://localhost:8081/healthz`; RabbitMQ management is available at `http://localhost:15672`. Events that were persisted but could not be published can be retried with:

```powershell
python -m orchestrator.outbox_relay --database .local/mail_ingest.sqlite3
```

Phase 1 reads recent messages from the Aliyun enterprise mailbox without
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
FEISHU_SEND_URL=https://your-feishu-adapter/messages
FEISHU_BOT_TOKEN=...
GIT_USERNAME=oauth2
GIT_PERSONAL_ACCESS_TOKEN=...
```

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
in the same one-to-one conversation.

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

## Project structure

The repository root owns shared configuration and the mailbox, GitLab,
requirement document, model API, and Feishu orchestration. The
`analyze-change-test-scope/` directory is an independent skill module consumed
by an analyzer adapter in a later phase.
