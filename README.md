# Mail to DingTalk Change Analysis Agent

本项目把邮件中的代码变更请求路由给指定钉钉用户，并由钉钉企业 Agent
使用 `analyze-change-test-scope` Skill 和 MCP 工具完成分析。

## Overall flow

1. `mail-ingest` 以只读方式扫描企业邮箱，不修改邮件已读状态。
2. 从回复邮件中的 `@姓名跟进测试` 提取唯一审核人姓名。
3. 使用 `.local/reviewers.json` 把姓名映射为稳定的钉钉 `userId`。
4. 生成 Agent 任务和任务级访问令牌，通过配置的钉钉入口投递给该用户。
5. 企业 Agent 读取上传的 Skill，调用 MCP 获取邮件任务、需求和仓库证据。
6. Agent 在钉钉对话中提出必要问题，获得回答后继续同一 Skill 流程。
7. Agent 输出报告，并调用 MCP 将最终结果归档。

邮件服务不调用第二个 LLM。分析、澄清和报告始终由同一个钉钉企业 Agent 完成。

## Information to configure

### Mailbox

| Value | Purpose | Where to get it |
|---|---|---|
| `ALIYUN_IMAP_USERNAME` | 扫描任务邮箱 | 企业邮箱账号 |
| `ALIYUN_IMAP_PASSWORD` | IMAP 登录 | 邮箱后台生成的客户端专用密码 |
| `ALIYUN_IMAP_HOST` | IMAP 服务 | 阿里企业邮箱默认 `imap.qiye.aliyun.com` |
| `MAIL_TRIGGER_SUFFIX` | 识别姓名 | 默认 `跟进测试`，匹配 `@张三跟进测试` |

### Reviewer mapping

复制 `.local/reviewers.example.json` 为 `.local/reviewers.json`：

```json
{
  "张三": {
    "dingtalk_user_id": "manager1234",
    "enabled": true
  }
}
```

`dingtalk_user_id` 必须是组织内稳定的用户 `userId`，不是姓名、手机号、群 ID
或临时会话 ID。可从钉钉管理后台通讯录导出，或使用钉钉通讯录接口按手机号查询。
运行账号必须具备读取对应人员 `userId` 的通讯录权限。

### Repository mapping

复制 `.local/projects.example.json` 为 `.local/projects.json`。项目名必须与邮件中
填写的项目名一致。`local_path` 是 MCP 服务所在机器可访问的完整仓库：

```json
{
  "market-admin": {
    "repository": "https://git.example/group/market-admin.git",
    "local_path": "D:/Project/market-admin",
    "base_branch": "master"
  }
}
```

`GIT_PERSONAL_ACCESS_TOKEN` 只需要仓库读取权限。服务通过进程级 HTTP Header
使用令牌，不会把令牌写入 Git remote 或分析结果。

### DingTalk Agent trigger

| Value | Purpose | Where to get it |
|---|---|---|
| `DINGTALK_AGENT_TRIGGER_URL` | 把任务主动送入企业 Agent | DEAP 的主动触发地址或工作流 Webhook |
| `DINGTALK_AGENT_TRIGGER_TOKEN` | 保护触发入口 | 对应入口生成的鉴权 Token；无鉴权时可留空，但生产环境不建议 |

DEAP 不同版本可能提供“主动触发”“工作流 Webhook”或企业内部网关。入口必须能接收
JSON，并最终让指定 `dingtalk_user_id` 收到 Agent 消息。若平台只能调用工作流，创建
一个工作流接收本项目 Payload，再调用企业 Agent。不要把普通群机器人 Webhook 当作
Agent 触发入口；群机器人只能发消息，不能自动建立完整 Agent Skill 会话。

触发 Payload 包含：

```json
{
  "event_type": "change_analysis.requested",
  "task_id": "<mail-message-id>",
  "agent_access_token": "<one-time-secret>",
  "recipient": {
    "name": "张三",
    "dingtalk_user_id": "manager1234"
  },
  "input": {
    "subject": "...",
    "projects": [{"project": "market-admin", "branch": "feature/x"}],
    "requirement_urls": ["https://..."]
  },
  "prompt": "Use the analyze-change-test-scope skill..."
}
```

### MCP

设置 `MCP_AUTH_TOKEN` 为长随机字符串，并通过 HTTPS 网关公开：

```text
https://agent-tools.example.com/mcp
```

在企业 Agent 的 `MCP -> 新建 MCP 服务` 中配置：

```text
类型: STREAMABLE HTTP
HTTP URL: https://agent-tools.example.com/mcp
Header: Authorization = Bearer <MCP_AUTH_TOKEN>
```

点击“MCP检测”后应看到：

- `get_mail_analysis_task`
- `prepare_change_workspace`
- `collect_change_context`
- `collect_repository_impact`
- `search_repository`
- `read_source_evidence`
- `release_change_workspace`
- `complete_mail_analysis_task`

`MCP_AUTH_TOKEN` 保护服务入口；邮件任务携带的 `agent_access_token` 只授权读取和完成
一个任务，在任务完成后失效。两者用途不同，不能复用。

## Skill configuration

将 `.local/analyze-change-test-scope-dingtalk.zip` 上传到企业 Agent 的 `Skill` 页面。
Skill 负责决定证据收集顺序、风险规则、澄清策略和报告格式；MCP 只负责确定性的
数据访问。企业 Agent 人设中加入：

```markdown
收到 change_analysis.requested 后，必须使用 analyze-change-test-scope Skill。
先调用 get_mail_analysis_task，再按 Skill 调用仓库证据工具。
不要自行编造项目、分支、需求或代码内容。
信息不足但不阻塞分析时，先给出有边界的结论并列入待确认。
只有无法确定项目或分支时才先询问用户。
完成后调用 complete_mail_analysis_task 归档最终报告。
```

## Run

```powershell
docker compose up --build rabbitmq change-analysis-mcp task-worker
docker compose --profile ingest run --rm mail-ingest
```

生产环境必须满足：MCP 使用 HTTPS、触发入口鉴权、仓库 Token 只读、
`.local/projects.json` 和 `.local/reviewers.json` 不提交 Git。

## Items requiring platform confirmation

实现已经为触发入口保留统一 HTTP 边界，但以下值必须在你的 DEAP 企业 Agent 环境确认：

1. 主动触发或工作流 Webhook 的真实 URL。
2. 入口鉴权 Header 和 Token 格式。
3. Payload 中指定接收人的字段是否直接使用 `userId`，还是由工作流节点映射。
4. 主动触发是否会创建可继续追问的 Agent 会话。
5. 是否允许 Agent 将 `agent_access_token` 作为 MCP 参数传递。

如果第 4 项不支持，应改为“给用户发送任务通知，用户点击进入 Agent 后由首条消息携带
task_id 和 token”，而不是假设机器人通知本身就是 Agent 会话。
