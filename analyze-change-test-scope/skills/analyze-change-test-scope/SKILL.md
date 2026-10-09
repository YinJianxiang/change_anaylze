---
name: analyze-change-test-scope
description: Historical compatibility alias for the canonical change-analysis Harness. Use it when a TAPD or DingTalk requirement URL, repository, target branch, and base branch are supplied.
---

# analyze-change-test-scope compatibility alias

This historical Skill name now delegates to the canonical Harness below. The old whole-repository call-graph implementation is retired and must not be used.

Canonical implementation:

```text
D:/Project/change_analyze/change-analysis-harness
```

# 代码变更分析

## 执行 Harness

```powershell
$Harness = "D:/Project/change_analyze/change-analysis-harness"
& "$Harness/run.ps1" -RequirementUrl "<TAPD 或钉钉文档链接>" -Branch "<目标分支>" -Repo "<仓库绝对路径>" -BaseBranch "master" -Output "D:/Project/change_analyze/.local/change-analysis.json"
```

## 读取证据

1. `requirement_documents`：需求标题、正文、来源和获取状态。
2. `repository.change_context`：Git 范围、变更文件、diff 和变更行。
3. `repository.repository_impact.java_ast`：Java AST 变更作用域和作用域内调用。
4. `repository.repository_impact.symbol_references`：变更符号的定向候选引用。
5. `selection_plan` / `impact_units`：文件纳入原因与分析单元分组。
6. `evidence_warnings` 和 `analysis_limits`：解析失败、截断和能力边界。

## 强制约束

- 分析模式应为 `Changed-scope AST evidence analysis`，不能写成全仓库分析。
- 必须检查 `analysis_scope`、`java_ast`、`repository_scan`、`selection_plan` 和 `analysis_limits`。
- `caller_candidates`、`symbol_references` 和 `api_references` 是候选证据，不得直接写成确定调用关系。
- 影响结论与测试点需带 `evidence_refs`；无锚点写入待确认。
- 不得重新构建旧版全仓库通用调用图。
- 不调用 OpenAI API、`llm_server` 或 Agent SDK。
- 不修改业务仓库。

## Codex 分析步骤

1. 从需求正文提取功能、验收、边界和接口约束。
2. 根据 `changed_symbols` 和 `java_ast.files[].changed_scopes` 确定实际变更的类、方法和构造器。
3. 根据 `calls_from_changed_scopes` 检查变更作用域内的下游调用。
4. 对定向引用候选查看源码，确认入口、调用方、数据库、配置和测试。
5. 区分已证实、候选和待确认事项。
6. 输出结论摘要、变更概览、需求追踪、影响范围、风险、测试点和待确认项。
