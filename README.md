# 代码变更分析 Skill

本项目只维护一个 Skill：**change-analysis**。它指导当前助手完成源码确认、需求完整性、影响判断、风险和测试范围分析，配套内部工具提供需求、Git diff、变更作用域与数据库只读证据，不调用外部模型服务。

- [唯一 Skill](<change-analysis-skill/skills/change-analysis/SKILL.md>)：分析步骤、证据边界和报告要求。
- [Skill 使用说明](<change-analysis-skill/README.md>)：安装、采集命令、配置与输出。
- [结构说明](<change-analysis-skill/docs/structure.md>)：模块职责与数据流。
- [Skill 目录说明](<change-analysis-skill/skills/README.md>)：Skill 与内部能力的边界。

## 安装

需要 Python 3.11+ 和 Git。从仓库根目录执行；已有根虚拟环境时跳过创建步骤：

```powershell
python -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -e ./change-analysis-skill
```

根虚拟环境是本地运行环境，继续保留但不纳入版本管理。根环境配置同样继续保留；按 [配置示例](<change-analysis-skill/.env.example>) 补充必需键，已有配置不要覆盖。只需离线 patch 分析时，无需需求服务凭证或 Git 远端权限。

## 采集与分析

```powershell
# 在仓库根目录运行，替换需求链接、目标分支与业务仓库路径。
& ./.venv/Scripts/python.exe -m change_analysis_skill `
  "https://www.tapd.cn/123/stories/view/456" "feature/example" `
  --repo D:/src/project --base-branch master --env-file .env `
  --output .local/change-analysis.json --reporter-dir reporter

# 只有 patch：不访问 Git 或需求服务，不采集 AST 或调用方。
& ./.venv/Scripts/python.exe -m change_analysis_skill `
  --diff-file changes.patch --output .local/patch.json --reporter-dir reporter
```

在线采集会从 `origin` 获取基准和目标分支，固定提交再分析，不切换业务仓库分支、不修改工作区。采集失败或证据降级必须如实保留，文本命中不能当作已确认调用关系。

返回 `READY_FOR_CODEX` 只表示证据已准备，`analysis` 为 `null`。当前助手仍须按 Skill 阅读统一指南、完整证据及相关源码，补齐业务判断和可执行测试场景。自动生成的 Markdown/HTML 是证据呈现，不是已完成的最终分析。

## 可选钉钉文档 Bridge

仅在需要通过钉钉 MCP 网关获取需求正文时安装可选依赖：

```powershell
& ./.venv/Scripts/python.exe -m pip install -e './change-analysis-skill[dingtalk]'
# Bridge 默认加载当前目录的 .env；已有进程环境变量优先。
$env:DINGTALK_MCP_URL = 'https://mcp-gw.dingtalk.com/server/...?key=...'
$env:DINGTALK_DOC_BRIDGE_TOKEN = '<本地共享令牌>'
& ./.venv/Scripts/Activate.ps1
python -m dingtalk_doc_bridge --host 127.0.0.1 --port 8091
```

在 Skill 使用的环境配置中设置 `DINGTALK_DOC_BRIDGE_URL=http://127.0.0.1:8091/v1/doc/content`，并配置相同的 `DINGTALK_DOC_BRIDGE_TOKEN`。保持 Bridge 终端运行；`GET http://127.0.0.1:8091/health` 只检查本地服务存活，不证明上游凭证可用。凭证、需求正文和 diff 不应提交或公开。

需求单独读取仅是 Skill 内部能力，不提供第二个 Skill 或独立产品入口。

## 可选 MySQL 只读取证

数据库查询是代码分析 Skill 的内部补证能力，不会随普通采集自动执行。兼容参考项目的 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`，也支持优先使用 `DATABASE_URL`。配置仅存放本地环境文件，模板不含真实凭据。

```powershell
& ./.venv/Scripts/python.exe -m pip install -e './change-analysis-skill[database]'
& ./.venv/Scripts/python.exe -m change_analysis_skill.database_cli `
  --env-file .env --sql 'SELECT 1 AS connected'
# 复杂查询可使用 --sql-file <文件> --params '[123]'（SQL 中使用 %s 占位符）。
```

仅提供单语句只读查询、服务器只读事务、连接/读写超时和结果行数上限，不提供数据库写入功能。应使用专用最小权限只读账户；SQL 检查和只读事务不能替代数据库权限，也不能保证未知存储函数无副作用。数据库结果用于核验表结构、历史数据和业务计算，不能替代源码实现核验；敏感数据和结果文件不得公开。

## 本地数据与验证

本地目录中的 `repos` 是业务 checkout，继续保留；采集 JSON、报告和缓存按需生成，不属于代码分发内容。不要为清理工具项目而删除业务 checkout 或根虚拟环境。

```powershell
& ./.venv/Scripts/python.exe -m unittest discover -s change-analysis-skill/tests
```

测试是否通过以实际执行结果为准；真实需求获取、Git 认证和钉钉 MCP 连通性需要对应环境另行验证。
