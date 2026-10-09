---
name: change-analysis
description: 当用户提供 TAPD 或钉钉需求文档链接、代码仓库、目标分支和基准分支，并要求分析代码变更、影响范围或测试点时使用。先调用 Harness 获取需求和变更作用域证据，再由 Codex 直接完成分析；不调用外部模型服务。
---

# 代码变更分析

## 执行 Harness

```powershell
$Harness = "D:/Project/change_analyze/change-analysis-harness"
& "$Harness/run.ps1" -RequirementUrl "<TAPD 或钉钉文档链接>" -Branch "<目标分支>" -Repo "<仓库绝对路径>" -BaseBranch "master" -Output "D:/Project/change_analyze/.local/change-analysis.json"
```

可用 `--preview` 先查看 `selection_plan` 与 `impact_units`，或设置 `ANALYSIS_EFFORT=low|medium|high`。

## 读取证据

1. `requirement_documents`：需求标题、正文、来源和获取状态。
2. `repository.change_context`：Git 范围、变更文件、diff 和变更行。
3. `repository.repository_impact.java_ast`：Java AST 变更作用域和作用域内调用。
4. `repository.repository_impact.symbol_references`：变更符号的定向候选引用。
5. `selection_plan`：进入分析的文件及裁剪/轻量/失败原因。
6. `impact_units`：按模块聚合的分析单元，含 `applicable_guide_sections`。
7. `evidence_warnings` 和 `analysis_limits`：解析失败、截断和能力边界。

## 强制约束

- 分析模式应为 `Changed-scope AST evidence analysis`，不能写成全仓库分析。
- 必须检查 `analysis_scope`、`java_ast`、`repository_scan`、`selection_plan` 和 `analysis_limits`。
- `caller_candidates`、`symbol_references` 和 `api_references` 是候选证据，不得直接写成确定调用关系。
- 只基于 `analysis_input` / `evidence_reference` / 候选对应源码区间确认；禁止把未确认候选写成已确认影响。
- 影响结论与测试点必须带 `evidence_refs`（`file` + `line|scope_id` + `kind`）；无锚点写入待确认。
- 优先按当前 `impact_unit.applicable_guide_sections` 阅读指南切片，而不是臆造范围外规则。
- 不得重新构建旧版全仓库通用调用图。
- 不调用 OpenAI API、`llm_server` 或 Agent SDK。
- 不修改业务仓库。

## Codex 分析步骤

1. 从需求正文提取功能、验收、边界和接口约束。
2. 阅读 `selection_plan` 与 `impact_units`，按 unit 覆盖变更文件。
3. 根据 `changed_symbols` 和 `java_ast.files[].changed_scopes` 确定实际变更的类、方法和构造器。
4. 根据 `calls_from_changed_scopes` 检查变更作用域内的下游调用。
5. 对定向引用候选查看源码，确认入口、调用方、数据库、配置和测试。
6. 区分已证实、候选和待确认事项；输出带 `evidence_refs` 的结论。
7. 对照报告模板自检：缺节、P0/P1 无测试点、未锚定却写“已确认”的项列入待确认。
8. 输出结论摘要、变更概览、需求追踪、影响范围、风险、测试点和待确认项。
