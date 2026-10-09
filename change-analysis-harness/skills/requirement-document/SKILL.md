---
name: requirement-document
description: 当用户提供 TAPD Story 或钉钉文档链接并要求获取、整理或理解需求内容时使用。通过本地 Harness 读取文档，返回结构化需求证据；不调用外部模型服务。
---

# 需求文档获取

这个 Skill 只负责把 TAPD 或钉钉文档读取为 Codex 可直接分析的结构化证据，不负责代码变更分析，也不调用外部 LLM、机器人、邮箱或 Worker。

## 适用输入

- TAPD Story 链接；
- 钉钉在线文档链接；
- 可选的输出 JSON 路径。

## 执行命令

~~~powershell
$Harness = "D:/Project/change_analyze/change-analysis-harness"
$Output = "D:/Project/change_analyze/.local/requirement-document.json"
& "$Harness/run-requirement.ps1" -RequirementUrl "<TAPD 或钉钉文档链接>" -Output $Output
~~~

Harness 默认从 D:/Project/change_analyze/.env 读取连接配置。也可以通过 -EnvFile 指定其他配置文件。不要把 Token、密码或签名值复制到报告中。

## 输出检查

读取输出 JSON，并确认：

- 顶层 status 为 READY_FOR_CODEX；
- operation 为 requirement_document_fetch；
- requirement_documents[].status 通常为 FETCHED；
- meta.title、meta.source 和正文内容可用；
- warnings 或 evidence_warnings 中的缺口需要在后续分析中披露。

其他状态的处理：PARTIAL 表示只使用已获取内容并列出缺失字段；FAILED 表示获取失败，不能假装需求已读取；UNSUPPORTED 表示链接类型暂不支持。

## 整理规则

1. 保留需求原文中的业务术语、字段名、接口名和枚举值。
2. 将正文整理为背景、目标、功能需求、验收标准、边界条件、异常处理和待确认项。
3. 不擅自补充需求；推断内容必须标记为“推断”。
4. 表格、列表、代码块和链接要保持可读，不能把字段拼成一段无结构文本。
5. 如果正文被截断、权限不足或只返回元数据，明确指出证据范围。

## 完成标准

输出一份可供 Codex 继续使用的需求摘要，包含来源、标题、正文范围、结构化需求项、验收标准和缺口。需要判断代码是否满足需求时，改用 change-analysis。
