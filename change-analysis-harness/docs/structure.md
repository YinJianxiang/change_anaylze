# Skill + Harness 结构说明

## 目标和保留结构

项目唯一 Skill 是 [change-analysis](<../skills/change-analysis/SKILL.md>)。它指导当前助手分析；Harness 只负责确定性证据采集，不代替业务判断、不调用外部模型、不修改业务代码。

```text
change_analyze/
|-- README.md
|-- .env                         本地必需配置，不提交
|-- .venv/                       保留的本地 Python 环境，不提交
|-- .local/repos/                保留的业务 checkout，不提交
`-- change-analysis-harness/
    |-- pyproject.toml
    |-- run.ps1
    |-- run-requirement.ps1      内部需求采集包装器
    |-- skills/
    |   |-- README.md
    |   `-- change-analysis/SKILL.md
    |-- docs/
    |-- tests/
    `-- src/
        |-- change_analysis_harness/
        |   |-- harness.py
        |   |-- git_service.py
        |   |-- requirement_service.py
        |   |-- requirement_cli.py
        |   |-- evidence/
        |   `-- guidance/
        `-- dingtalk_doc_bridge/ 可选 extra dingtalk
```

运行时可再生成采集 JSON、报告和缓存；它们不是源码分发内容。清理本地临时产物不能删除业务 checkout 或根虚拟环境。

## 模块职责

| 模块 | 职责 |
|---|---|
| [主包装器](<../run.ps1>) | 使用根虚拟环境，组装采集参数，缺环境时给出安装提示 |
| [Harness](<../src/change_analysis_harness/harness.py>) | 组合需求、Git 和变更证据，输出 JSON 与证据报告 |
| [需求服务](<../src/change_analysis_harness/requirement_service.py>) | 路由 TAPD/钉钉正文获取 |
| [内部需求 CLI](<../src/change_analysis_harness/requirement_cli.py>) | 单独获取需求，用于内部采集、排查和测试 |
| [Git 服务](<../src/change_analysis_harness/git_service.py>) | 获取远端引用，固定 base/head/merge-base 与 diff |
| [数据库只读工具](<../src/change_analysis_harness/database.py>) | 可选 MySQL 连接、单语句检查、只读事务和有界结果取证；不随普通采集自动执行 |
| [变更上下文](<../src/change_analysis_harness/evidence/collect_change_context.py>) | 变更文件、行号和 unified diff |
| [Java AST](<../src/change_analysis_harness/evidence/java_ast.py>) | 变更作用域与作用域内调用证据 |
| [影响证据](<../src/change_analysis_harness/evidence/collect_repository_impact.py>) | 组合变更作用域和定向候选引用 |
| [统一指南](<../src/change_analysis_harness/guidance/impact-and-test-guide.md>) / [报告模板](<../src/change_analysis_harness/guidance/report-template.md>) | Skill 与证据报告共享的分析规范 |

## 数据流

```text
TAPD/钉钉链接 ----------> 需求正文及获取状态
origin 基准/目标分支 ----> 固定提交与 diff -> 变更行
                                            |-> Java AST 变更作用域
                                            |-> 非 Java 轻量行级证据
                                            `-> 变更符号定向 git grep
或离线 patch ----------> diff 与规则候选（无需求/AST/调用方）
上述证据 -> selection_plan + impact_units + 分析指南
         -> 当前助手阅读源码确认 -> 业务影响、风险和测试范围报告
```

可选钉钉 Bridge 将 MCP 文档网关转换为本地 HTTP 正文接口。通过 `pip install -e './change-analysis-harness[dingtalk]'` 安装依赖后，先配置进程环境，再运行 `python -m dingtalk_doc_bridge --host 127.0.0.1 --port 8091`。不需要钉钉需求时无须安装或启动。完整配置见 [Harness 说明](<../README.md>)。

## 证据边界

1. 只解析 diff 命中的文件；Java 只保留与旧/新版本变更行相交的 AST 作用域。
2. 定向文本引用只是候选，不是已确认调用关系；规则命中也不是已确认缺陷。
3. 不构建全仓库 AST 或通用调用图；动态调用、配置驱动、生成代码和跨仓库依赖由助手结合源码确认。
4. `selection_plan` 和 `impact_units` 是确定性采集规划，不是最终业务结论。必须检查裁剪标记、限制与失败告警。
5. `READY_FOR_CODEX`、自动 Markdown 和 HTML 都不代表最终分析完成。未运行的测试不写通过，证据缺口如实列出。
