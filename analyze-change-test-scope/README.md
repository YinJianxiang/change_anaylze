# Analyze Change Test Scope

一个面向 Codex 的开源 Skill：读取前端和后端 Git 代码变更，分析需求与影响范围，并生成带证据、风险等级和优先级的测试清单。

## 能做什么

- 分析工作区、暂存区、单个提交、Git revision range 或 GitHub PR URL
- 安全准备 PR 的完整仓库上下文，并在独立 worktree 中定位变更
- 对完整仓库执行全项目源码扫描，建立轻量级 `caller → callee` 调用关系
- 搜索变更 Symbol、接口路径、前后端消费者、测试、配置、任务和消息消费者
- 串联前端页面、组件、API、后端服务、数据库和外部依赖
- 检查接口契约、权限、数据迁移、配置和部署风险
- 输出 P0、P1、P2 分级的测试场景和回归范围
- 区分代码事实、影响推断和待人工确认事项
- 使用 Python 标准库和本地 Git，不依赖第三方 Python 包

## 两种分析模式

### Mode A：仅代码变更

适用于没有需求文档的情况。Skill 根据 diff 推断可能的业务意图，标注推断置信度，并输出直接影响、间接回归范围和必测场景。

```text
使用 $analyze-change-test-scope 分析当前工作区的前后端改动，
输出需要测试的内容。
```

### Mode B：需求对照

同时读取需求、验收标准或任务说明，对照代码改动生成需求追踪矩阵，并识别：

- 已覆盖或部分覆盖
- 未发现实现
- 实现与需求冲突
- 疑似超范围实现

```text
使用 $analyze-change-test-scope，对照 docs/requirement.md
分析 main...当前分支的代码改动，输出需求覆盖情况和测试范围。
```

## 安装

需要 Git、Python 3.9+ 和支持 Skills 的 Codex。

```bash
git clone https://github.com/Cheryl-station/analyze-change-test-scope.git
cp -R analyze-change-test-scope/skills/analyze-change-test-scope ~/.codex/skills/
```

重新打开 Codex 会话后，可通过 `$analyze-change-test-scope` 显式调用。

## 支持的变更来源

Skill 自带只读采集脚本：

```bash
python3 skills/analyze-change-test-scope/scripts/collect_change_context.py --repo .
python3 skills/analyze-change-test-scope/scripts/collect_change_context.py --repo . --staged
python3 skills/analyze-change-test-scope/scripts/collect_change_context.py --repo . --commit HEAD
python3 skills/analyze-change-test-scope/scripts/collect_change_context.py --repo . --range main...HEAD
```

GitHub PR 可先准备独立分析工作区，再采集范围变更与全仓影响：

```bash
python3 skills/analyze-change-test-scope/scripts/prepare_pr_workspace.py \
  --pr-url https://github.com/owner/repo/pull/123

python3 skills/analyze-change-test-scope/scripts/collect_change_context.py \
  --repo WORKSPACE --range MERGE_BASE...HEAD_SHA \
  --analysis-mode full --pr-number 123 --pr-info-source gh

python3 skills/analyze-change-test-scope/scripts/collect_repository_impact.py \
  --repo WORKSPACE --range MERGE_BASE...HEAD_SHA
```

Codex 调用示例：

```text
使用 $analyze-change-test-scope 分析：
https://github.com/owner/repo/pull/123

请拉取完整仓库上下文，分析方法调用方、接口消费者、
数据库依赖、相关测试和完整回归范围。
```

脚本输出可稳定解析的结构化 JSON，不修改业务代码、不自动运行测试或安装依赖。Diff 采集默认不读取未跟踪文件内容，也不对二进制文件执行文本转换；全仓搜索会限制文件大小、单对象命中数和总结果数，并报告截断情况。

## 全项目源码扫描与轻量级调用关系

`collect_repository_impact.py` 不只读取 PR diff，也会遍历完整仓库中的全部受支持源码文件。扫描范围包括 Python、JavaScript、TypeScript、JSX/TSX、Java 和 Vue；明确排除 `.git`、`node_modules`、虚拟环境、构建产物、覆盖率目录、生成代码和 vendor 目录。

扫描过程会：

1. 读取所有受支持的项目源码，建立函数、方法、类和模块入口索引。
2. 提取每个函数或方法内部的调用点。
3. 按 Symbol 名称建立轻量级 `caller → callee` 关系，并保留文件、行号和短代码证据。
4. 将调用关系与本次变更对象关联，反向查找直接调用方和多跳调用链。
5. 输出扫描文件数、定义数、调用点数、完成状态、解析限制和截断状态。

Python 使用标准库 AST 建立关系；JavaScript、TypeScript、Java 和 Vue 使用正则与花括号作用域规则。该结果是适合测试影响分析的轻量级调用图，不等同于编译器级完整调用图；重载、继承、动态分派、反射、运行时注入和生成代码会明确列为分析限制。

关键 JSON 结构示例：

```json
{
  "repository_scan": {
    "candidate_source_files": 1250,
    "source_files_scanned": 1250,
    "definitions_indexed": 8300,
    "call_sites_indexed": 17600,
    "scan_complete": true
  },
  "lightweight_call_graph": {
    "nodes": [],
    "edges": [
      {
        "caller": "backend/order_service.py::create_order@42",
        "callee_symbol": "calculate_amount",
        "resolved_callees": ["backend/amount.py::calculate_amount@18"],
        "file": "backend/order_service.py",
        "line": 47,
        "confidence": "medium"
      }
    ],
    "impact_paths": [
      {
        "changed_symbol": "calculate_amount",
        "path": [
          {"symbol": "submit_order", "file": "backend/controller.py"},
          {"symbol": "create_order", "file": "backend/order_service.py"},
          {"symbol": "calculate_amount", "file": "backend/amount.py"}
        ]
      }
    ]
  }
}
```

当 `scan_complete` 为 `false` 时，必须结合文件上限、超时、解析失败和截断字段说明未完成扫描的范围，不能声称已经覆盖全部调用方。

## PR 分析安全性与降级

- 不直接修改被分析仓库，也不在用户活跃分支执行 `git pull`、`git reset`、`git checkout` 或强制切换分支。
- 已有目标仓库时只安全 fetch PR refs，再创建 detached 临时 worktree；没有目标仓库时克隆到独立缓存目录。
- 优先使用 `gh` 获取 PR 信息，无法使用时回退到 GitHub API，并提供清晰错误。
- 完整仓库不可用时继续使用现有 diff/patch，明确标记为 `Diff-only analysis`，不声称已确认全部间接调用方。
- 仓库与 diff 可能包含敏感代码；JSON 只保留短代码证据，分享报告前仍需脱敏。

## 输出内容

报告通常包括：

- 分析模式、Git 范围及限制
- 改动事实和可能的需求意图
- 前后端影响链路
- 高、中、低风险判断
- P0/P1/P2 测试场景
- 核心和定向回归范围
- Mode B 的需求追踪矩阵
- 待确认问题和分析盲区

测试场景会尽量包含前置数据、操作、可观察的预期结果以及对应代码证据。

## 项目结构

```text
.
├── README.md
├── LICENSE
└── skills/
    └── analyze-change-test-scope/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/
        └── scripts/
            ├── collect_change_context.py
            ├── collect_repository_impact.py
            └── prepare_pr_workspace.py
```

## 使用边界

代码差异无法完整还原产品需求、线上动态配置、外部系统状态或隐含业务规则。本 Skill 用于辅助测试范围分析，不代替需求评审、代码评审和最终发布判断。

代码 diff 和完整仓库可能包含敏感信息。请只在受信任的本地环境中分析，并在分享报告前进行脱敏。

## License

[Apache License 2.0](LICENSE)
