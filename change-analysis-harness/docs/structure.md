# Harness 结构说明

## 目标

Harness 是“证据采集器”，不是“通用代码分析器”。它只采集需求、Git diff 和变更作用域证据，Codex 负责最终理解和报告。

## 模块职责

| 模块 | 职责 |
|---|---|
| `run.ps1` | 组装输入并调用 Harness |
| `harness.py` | 组合需求和仓库证据 |
| `requirement_service.py` | 路由 TAPD/钉钉需求获取 |
| `git_service.py` | 解析分支、merge-base、提交和 diff |
| `collect_change_context.py` | 输出变更文件、行号和 unified diff |
| `java_ast.py` | 解析 Java 变更 AST 作用域和作用域内调用 |
| `collect_repository_impact.py` | 组合变更作用域和定向引用证据 |
| `selection.py` | `selection_plan` 与确定性 `impact_units` |
| `guide_match.py` | 按文件路径匹配指南切片 |
| `coverage_reflector.py` | 分析结论覆盖自检 |

## 数据流

```text
TAPD/钉钉链接 --------> requirement_documents
基准分支 + 目标分支 -> Git diff -> 变更行
                                      |-> Java AST 变更作用域
                                      |-> 非 Java 变更行证据
                                      `-> 变更符号定向 git grep
上述证据 -> selection_plan + impact_units -> Codex / 机器人分析
分析结论 -> CoverageReflector -> 待确认缺口
```

## 变更作用域原则

1. 只解析 diff 命中的文件。
2. Java 只保留与变更行相交的 AST 节点。
3. 同时读取基准版本和目标版本，标记 `added`、`modified` 和 `deleted`。
4. 作用域外的搜索只是定向候选引用，不视为确认调用关系。
5. 不构建全仓库 AST、轻量调用图或通用正则调用图。

## 边界

- Harness 不调用外部模型。
- Harness 不运行机器人、邮箱、RabbitMQ 或 Worker。
- Harness 不自动修改业务代码。
- 反射、动态分派、配置驱动、生成代码和跨仓库依赖需要 Codex 结合源码确认。
