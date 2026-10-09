# 代码变更分析 Harness

这是一个可独立安装、与 Codex Skill 配合使用的本地证据采集工具，也是机器人复用的唯一证据采集实现。Harness 只负责获取 TAPD/钉钉需求、Git diff 和变更作用域证据；最终的业务理解、影响判断、报告和测试点由当前 Codex 直接完成。

## 设计边界

- 获取 TAPD Story 或钉钉在线文档。
- 读取基准提交和目标提交之间的 Git diff。
- 只分析 diff 命中的文件、变更行和 AST 作用域。
- Java 使用 `tree-sitter-java`，不使用旧版 Java 正则解析。
- 变更作用域之外只执行变更符号的定向 `git grep`，不构建全仓库调用图。
- 不调用 OpenAI API、`llm_server`、Agent SDK、机器人、邮箱或 Worker。
- 不修改业务仓库。

## 代码变更分析流程

1. 获取需求文档。
2. 解析基准分支和目标分支的 merge-base、提交和 diff。
3. 计算每个变更文件的旧版本变更行和新版本变更行。
4. Java 文件只解析与变更行相交的类、接口、枚举、record、方法和构造器。
5. 提取变更 Java 作用域内的方法调用、构造器调用和方法引用。
6. 非 Java 文件只保留变更行级的轻量符号证据，不扫描完整文件。
7. 针对变更符号执行定向 `git grep`，收集候选调用方、测试、接口、数据库和配置线索。
8. Codex 根据需求、diff、AST 作用域和候选引用进行二次确认。

## 关键输出

- `repository`：完整采集结果，保留采集器返回的全部条目和 Git 快照 diff。
- `analysis_input.repositories`：供 Codex 首次阅读的摘要，列表裁剪会标记 `_truncated_items`。
- `analysis_input.evidence_reference`：完整证据的位置；`json_pointer` 为 `/repository`。
- `selection_plan`：每个变更文件的纳入状态（`included` / `non_java_light` / `truncated` / `summary_only` / `evidence_failed`）与原因。
- `impact_units`：确定性分组的分析单元（文件、AST 作用域、候选引用、适用指南切片、`diff_byte_estimate`）。
- `effort`：来自 `--effort` 或环境变量 `ANALYSIS_EFFORT`（`low` / `medium` / `high`，默认 `medium`）。
- 指定 `--output .local/change-analysis.json` 时，同时写出 `.local/change-analysis.analysis-input.json` 摘要，其中 `evidence_reference.path` 为完整 JSON 的绝对路径。
- 不指定 `--output` 时，stdout 返回包含完整证据与摘要的 JSON，引用指向同一结果中的 `/repository`，不会额外写摘要文件。
- `--max-diff-bytes` 只限制摘要的 diff，完整证据保留原始 diff。完整采集结果仍受采集器自身搜索上限和解析能力限制，需检查 `analysis_limits`。
- `--preview`：仍采集完整证据，stdout 先打印 selection/impact_units 门禁表，`status=PREVIEW`，默认不写 Markdown 报告。
- Markdown 报告使用完整采集结果，并包含 Selection / ImpactUnits 节。

Git 上下文和影响证据独立采集。任一阶段失败时保留另一阶段的成功结果，失败字段为 `null`，`evidence_warnings` 标明失败阶段；两阶段均失败时仍保留 Git 快照和 diff。

- `analysis_mode`：两阶段成功为 `Changed-scope AST evidence analysis`，部分成功为 `Partial evidence analysis`，均失败或缺少提交信息为 `Diff-only evidence analysis`。
- `repository.repository_impact.analysis_scope`: `changed_files_and_changed_ast_scopes_only`。
- `repository.repository_impact.java_ast.files`: Java AST 变更作用域。
- `repository.repository_impact.repository_scan.scan_scope`: `changed_files_only`。
- `repository.repository_impact.repository_scan.whole_repository_ast_scan`: `false`。
- `caller_candidates`、`symbol_references` 和 `api_references`: 候选证据，不是已确认调用边。

## 目录结构

```text
change-analysis-harness/
|-- README.md
|-- run.ps1
|-- run-requirement.ps1
|-- skills/
|   |-- change-analysis/SKILL.md
|   `-- requirement-document/SKILL.md
|-- docs/
|   |-- structure.md
|   |-- impact-and-test-guide.md
|   `-- report-template.md
`-- src/change_analysis_harness/
    |-- harness.py
    |-- git_service.py
    |-- requirement_service.py
    `-- evidence/
        |-- collect_change_context.py
        |-- collect_repository_impact.py
        |-- java_ast.py
        `-- security.py
```

## 使用

```powershell
& D:/Project/change_analyze/change-analysis-harness/run.ps1 `
  -RequirementUrl "https://www.tapd.cn/..." `
  -Branch "feature/example" `
  -Repo "D:/src/your-project" `
  -BaseBranch "master" `
  -Output "D:/Project/change_analyze/.local/change-analysis.json"
```

如果缺少 Java AST 依赖，运行：

```powershell
python -m pip install -e D:/Project/change_analyze/change-analysis-harness
```


## Markdown report output

The Harness writes a JSON evidence file and automatically groups the Markdown report by requirement:

```text
reporter/
|-- <requirement-title>_<story-id>/
    `-- change-analysis-report.md
```

Example:

```powershell
& D:/Project/change_analyze/change-analysis-harness/run.ps1 `
  -RequirementUrl "https://www.tapd.cn/..." `
  -Branch "feature/example" `
  -Repo "D:/src/your-project" `
  -BaseBranch "master" `
  -Output "D:/Project/change_analyze/.local/change-analysis.json"
```

By default, the reporter directory is `D:/Project/change_analyze/reporter`. Use `-ReporterDir` to override it, `-ReportOutput` to specify an exact report path, or `-NoReport` to disable Markdown output.

## 与机器人的关系

`orchestrator/` 单向依赖本包。本包不依赖机器人，不需要安装根目录 `requirements.txt`。机器人项目配置只在其适配模块中处理，需求读取、Git 和证据算法均在本包维护。

共享分析指南和报告覆盖项位于 `src/change_analysis_harness/guidance/`，随 Python 包分发。`docs/` 和历史 Skill 引用该唯一来源。Codex Skill 与机器人执行指令分开维护，各自使用同一份分析规则。

从仓库根目录安装和验证：

```powershell
python -m pip install -e ./change-analysis-harness
python -m unittest discover -s change-analysis-harness/tests
```

CLI 返回 `READY_FOR_CODEX` 不表示分析完成；生成的 Markdown 是证据报告，最终业务判断由 Codex 或机器人模型完成。
