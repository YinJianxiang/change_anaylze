# 代码变更分析 Harness

Harness 是 [change-analysis Skill](<skills/change-analysis/SKILL.md>) 的确定性证据采集层。它获取需求、固定 Git 提交、diff、Java 变更作用域 AST 和定向引用候选；当前助手负责源码确认、影响判断和最终报告。项目只有这一个 Skill，需求读取是内部能力。

## 安装和配置

Python 3.11+、Git；以下命令从仓库根目录执行。保留已有根虚拟环境，不必重复创建：

```powershell
python -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-harness
```

参考 [配置示例](<.env.example>) 编辑根环境配置，已有配置不要覆盖：

```dotenv
HARNESS_REPOSITORIES='{"backend":"D:/src/backend","frontend":"D:/src/frontend"}'
HARNESS_REPO=D:/src/backend
HARNESS_BASE_BRANCH=master
ANALYSIS_EFFORT=medium
```

- 用 `--repository-name backend` / `-RepositoryName backend` 从 JSON 映射选仓库；否则使用 `HARNESS_REPO`。显式 `--repo` / `-Repo` 优先。
- 环境配置中的相对仓库路径以所选环境文件目录为基准；CLI 路径以调用时当前目录为基准。已有进程环境变量优先于文件配置。
- Git 可使用凭据助手；按需配置 `GIT_USERNAME`、`GIT_PERSONAL_ACCESS_TOKEN`。TAPD 需求按需配置 `TAPD_ACCESS_TOKEN` 和 `TAPD_API_BASE_URL`。
- Python CLI 用 `--env-file .env` 显式选择配置。PowerShell 包装器优先使用 Harness 目录下的环境文件，不存在时回退根环境文件；可用 `-EnvFile` 覆盖。

## 在线证据采集

```powershell
& ./.venv/Scripts/python.exe -m change_analysis_harness `
  "https://www.tapd.cn/123/stories/view/456" "feature/example" `
  --repo D:/src/project --base-branch master --env-file .env `
  --output .local/change-analysis.json --reporter-dir reporter

# 包装入口使用根虚拟环境，继承调用者的输出路径语义。
& ./change-analysis-harness/run.ps1 `
  -RequirementUrl "https://www.tapd.cn/123/stories/view/456" `
  -Branch "feature/example" -Repo D:/src/project -BaseBranch master `
  -Output .local/change-analysis.json
```

每次在线执行（包括 `--preview`）都从 `origin` fetch 基准与目标分支，失败立即停止，不使用旧引用继续分析。不提供 `--no-fetch`。采集固定 base/head/merge-base，不执行 `git pull`、不切分支、不修改业务工作区。

证据只覆盖 diff 命中的文件及变更行：Java 用 `tree-sitter-java` 解析相交的类、接口、枚举、record、方法和构造器；其他语言保留变更行级轻量符号证据。作用域外只对变更符号定向 `git grep`，不构建全仓库 AST 或调用图。`caller_candidates`、`symbol_references`、`api_references` 都是待源码确认的候选。

## 离线 patch 和确定性风险提示

```powershell
& ./.venv/Scripts/python.exe -m change_analysis_harness `
  --diff-file changes.patch --output .local/patch.json `
  --report-output .local/patch.md --no-archify `
  --risk-exclude dist build node_modules --fail-on high

& ./change-analysis-harness/run.ps1 -DiffFile changes.patch `
  -RiskExclude dist,build,node_modules -FailOn high
```

离线模式不访问 Git、需求服务或模型，不采集 AST/调用方。没有完整仓库时，不能确认所有间接调用方和完整回归范围。

`repository.risk_scan` 检查新增行中的疑似字面量凭证、动态执行、SQL 拼接、shell 调用、宽异常、删除操作、调试输出和跳过测试。候选含规则编号、严重度、文件、新版本行号及 `status=candidate`；不做数据流或跨行语义分析，规则命中不是已确认缺陷，零命中也不代表代码正确。原始 diff 仍可能包含敏感数据。

`--risk-exclude` 按完整目录段匹配，只影响规则扫描，不移除原始 diff 或其他证据。摘要最多展示 50 条候选，完整 JSON 和 CI 门禁使用全部候选。默认不启用风险退出码；显式 `--fail-on low|medium|high` 后，达到阈值返回 1，输入或执行错误返回 2，报告和 JSON 在门禁返回前写出。

## 输出与最终分析

- `repository`：完整证据；采集器自身上限与失败情况见 `analysis_limits`、`evidence_warnings`。
- `analysis_input.repositories`：摘要，裁剪项以 `_truncated_items` 标记。
- `analysis_input.evidence_reference`：完整证据引用，`json_pointer` 为 `/repository`。指定 `--output` 时额外生成同目录的 `<stem>.analysis-input.json`；未指定时 stdout 的 JSON 同时包含摘要与完整证据。
- `selection_plan`、`impact_units`：确定性纳入状态和分析单元，不是模型结论。`--max-diff-bytes` 只裁剪摘要 diff。
- `--effort low|medium|high` / `ANALYSIS_EFFORT`：分析工作量提示，默认 `medium`；不会触发外部模型调用。
- `--preview`：仍采集完整证据，输出纳入/分组门禁表，状态为 `PREVIEW`，默认不生成 Markdown/HTML。

Git 上下文与影响证据独立采集，一阶段失败仍保留另一阶段成功结果。实际范围分别标记为 `Changed-scope AST evidence analysis`、`Partial evidence analysis` 或 `Diff-only evidence analysis`，不得写成全仓库分析。

默认输出 `reporter/<需求分组>/change-analysis-report.md` 及 HTML 影响图。`--reporter-dir` 修改报告根目录，`--report-output` 指定 Markdown 精确路径，`--no-report` 关闭 Markdown；HTML 使用独立的 `--no-archify` / `--archify-output` 控制。HTML 只呈现相同证据，交互库从 CDN 加载，离线退化为静态大纲；候选关系不等于确认调用边。

PowerShell 包装器仅暴露其参数表中的选项；需要 `--preview`、`--effort`、`--no-archify`、`--archify-output` 等高级选项时使用 Python CLI，可运行 `python -m change_analysis_harness --help` 检查实际支持项。

`READY_FOR_CODEX` 不表示分析完成；`analysis` 为 `null`。当前助手必须阅读 `analysis_input.analysis_guidance` 和必要源码，按 [统一指南](<src/change_analysis_harness/guidance/impact-and-test-guide.md>) 与 [报告模板](<src/change_analysis_harness/guidance/report-template.md>) 给出业务结论、影响链路和测试场景。自动 Markdown 只是证据草稿。只有实际执行的测试才能报告通过，缺失证据明确列为待确认。

## 可选钉钉文档 Bridge

Bridge 随本项目可选安装，不是独立 Skill。仅需钉钉 MCP 网关时安装：

```powershell
& ./.venv/Scripts/python.exe -m pip install -e './change-analysis-harness[dingtalk]'
$env:DINGTALK_MCP_URL = 'https://mcp-gw.dingtalk.com/server/...?key=...'
$env:DINGTALK_DOC_BRIDGE_TOKEN = '<本地共享令牌>'
& ./.venv/Scripts/Activate.ps1
python -m dingtalk_doc_bridge --host 127.0.0.1 --port 8091
```

Bridge 默认加载当前目录的 `.env`，可用 `--env-file <路径>` 选择配置，已有进程环境变量优先。MCP URL 需使用已授权的实际网关地址；其中的 key 视为凭证。保持服务终端运行，在 Harness 使用的配置中设置：

```dotenv
DINGTALK_DOC_BRIDGE_URL=http://127.0.0.1:8091/v1/doc/content
DINGTALK_DOC_BRIDGE_TOKEN=<与 Bridge 一致的令牌>
```

`GET http://127.0.0.1:8091/health` 仅检查服务存活，真实文档拉取才验证上游授权。无须钉钉 MCP 时不安装 extra、不启动服务。

## 数据库只读取证（可选）

安装 `database` extra 后，可用内部数据库 CLI 定向检查 MySQL 表结构、历史数据和计算依据；不提供写操作，也不会在普通采集中自动联网。

```powershell
& ./.venv/Scripts/python.exe -m pip install -e './change-analysis-harness[database]'
& ./.venv/Scripts/python.exe -m change_analysis_harness.database_cli `
  --env-file .env --sql 'SELECT 1 AS connected'
& ./.venv/Scripts/python.exe -m change_analysis_harness.database_cli `
  --env-file .env --sql-file .local/evidence.sql --params '[123]' `
  --max-rows 100 --timeout 10 --output .local/database-evidence.json
```

- 配置兼容 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`，`DATABASE_URL` 设置时优先；URL 支持 `mysql://` 和 `mysql+pymysql://`，特殊字符需 URL 编码。已有进程变量优先于环境文件。
- SQL 使用 `%s` 占位符与 JSON 数组参数；只允许单条只读语句。驱动禁用多语句，服务器必须成功进入只读事务，否则拒绝执行；结束时回滚并关闭连接。
- `--timeout` 用于连接及 socket 读写超时，不是端到端执行时限；超时不保证服务端查询立即停止。避免全表重查询，优先索引条件、元数据和脱敏聚合。
- 结果限制行数并标记截断，不代表扫描了全部数据；数据库结果不会自动合并进 Git 证据。助手需记录查询目的、环境、时间和数据口径后，补充到统一报告的相应章节。
- 必须使用专用最小权限只读账户。SQL 词法防护和只读事务不能替代数据库权限；未知存储函数可能有副作用，不应执行。凭据不写报告，结果包含敏感数据时需脱敏。

## 内部需求读取与验证

[需求 CLI](<src/change_analysis_harness/requirement_cli.py>) 和 [需求包装器](<run-requirement.ps1>) 仅用于内部采集、排查和测试，不构成第二个 Skill。其状态按正文实际获取情况返回：完整为 `READY_FOR_CODEX`（0），部分或截断为 `PARTIAL`（1），无可用正文为 `FAILED`、不支持链接为 `UNSUPPORTED`（2）；失败和部分结果仍保留 JSON，应检查各文档状态。

```powershell
& ./.venv/Scripts/python.exe -m unittest discover -s change-analysis-harness/tests
```

模块职责见 [结构说明](<docs/structure.md>)；Skill 边界见 [Skill 目录说明](<skills/README.md>)。
