"""Render Skill evidence as a portable Markdown report."""
from __future__ import annotations

from datetime import date
import re
from pathlib import Path
from typing import Any, Iterable


def _value(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _cell(value: Any) -> str:
    return _value(value).replace("|", r"\|").replace("\n", "<br>")


def _items(values: Iterable[Any], empty: str = "未提供", limit: int | None = None) -> str:
    rows = list(values)
    omitted = 0
    if limit is not None and len(rows) > limit:
        omitted = len(rows) - limit
        rows = rows[:limit]
    if not rows:
        return empty
    rendered = []
    for value in rows:
        if isinstance(value, dict):
            if "_truncated_items" in value:
                rendered.append(f"- Skill 截断了 {value.get('_truncated_items')} 项证据。")
                continue
            text = value.get("path") or value.get("symbol") or value.get("qualified_name") or value.get("name") or str(value)
        else:
            text = str(value)
        rendered.append(f"- `{text}`")
    if omitted:
        rendered.append(f"- 报告大小限制省略了其余 {omitted} 项证据。")
    return "\n".join(rendered)


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        values = list(row)
        values.extend(["-"] * (len(headers) - len(values)))
        lines.append("| " + " | ".join(_cell(value) for value in values[: len(headers)]) + " |")
    return "\n".join(lines)


def _requirement_summary(documents: list[dict[str, Any]]) -> tuple[str, str]:
    titles: list[str] = []
    contents: list[str] = []
    for document in documents:
        meta = document.get("meta") or {}
        title = meta.get("title") or document.get("url") or "未命名需求"
        titles.append(str(title))
        content = document.get("content")
        if isinstance(content, str) and content.strip():
            contents.append(content.strip())
    return "、".join(titles) if titles else "未获取需求文档", "\n\n".join(contents)


_INVALID_DIRECTORY_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def requirement_directory_name(result: dict[str, Any]) -> str:
    """Return a safe, stable directory name for one requirement."""
    documents = result.get("requirement_documents") or []
    title = ""
    story_id = ""
    if documents:
        first = documents[0] or {}
        meta = first.get("meta") or {}
        title = str(meta.get("title") or "")
        story_id = str(meta.get("story_id") or "")
    if not title:
        request = result.get("request") or {}
        title = str(request.get("requirement_url") or
                    ("patch_" + Path(request["diff_file"]).stem if request.get("diff_file") else "requirement"))
    name = _INVALID_DIRECTORY_CHARS.sub("_", title)
    name = re.sub(r"\s+", " ", name).strip(" ._") or "requirement"
    if story_id:
        safe_story_id = _INVALID_DIRECTORY_CHARS.sub("_", story_id).strip(" ._")
        if safe_story_id and safe_story_id not in name:
            name = f"{name}_{safe_story_id}"
    return name[:150].rstrip(" ._") or "requirement"


def default_report_path(result: dict[str, Any], reporter_dir: Path) -> Path:
    """Build reporter/<requirement>/change-analysis-report.md."""
    return reporter_dir / requirement_directory_name(result) / "change-analysis-report.md"


def render_markdown(result: dict[str, Any], *, generated_on: str | None = None) -> str:
    """Render automatic evidence, not unverified business analysis or test results."""
    request = result.get("request") or {}
    repository = result.get("repository") or {}
    impact = repository.get("repository_impact") or {}
    context = repository.get("change_context") or {}
    documents = result.get("requirement_documents") or []
    title, requirement_content = _requirement_summary(documents)
    mode_b = bool(requirement_content)
    generated_on = generated_on or date.today().isoformat()
    analysis_mode = result.get("analysis_mode") or repository.get("analysis_mode") or "未提供（无法确认仓库上下文）"
    patch_only = bool(request.get("diff_file"))
    diff_only = "diff-only" in str(analysis_mode).lower() or patch_only
    repository_source = repository.get("repository_source") or (
        "仅 patch" if patch_only else "现有本地仓库" if (
            repository.get("local_path") or request.get("repository") or repository.get("repository")
        ) else "未确认"
    )
    diff_truncated = repository.get("diff_truncated_for_analysis")
    if diff_truncated is None:
        diff_truncated = context.get("diff_truncated")
    scan = impact.get("repository_scan") or {}
    quality = (impact.get("impact_slice") or {}).get("graph_quality") or {}
    changed_symbols = impact.get("changed_symbols") or []
    related_tests = impact.get("related_tests") or []
    selection = result.get("selection_plan") or (result.get("analysis_input") or {}).get("selection_plan") or {}
    units = result.get("impact_units") or (result.get("analysis_input") or {}).get("impact_units") or []
    selection_files = selection.get("files") or []
    if not selection_files:
        selection_files = [item for plan in selection.get("repositories") or [] for item in plan.get("files") or []]
    changed_files = context.get("changed_files") or [
        {"path": path} if isinstance(path, str) else path for path in repository.get("changed_files") or []
    ]
    whole_scan = scan.get("whole_repository_ast_scan") is True
    changed_scope = "changed" in str(impact.get("analysis_scope") or analysis_mode).lower()
    full_scan = whole_scan and scan.get("scan_complete") is True and not changed_scope and not diff_only
    pending = "待分析：自动证据尚未确认业务影响"
    lines = [
        "# 代码变更测试范围报告", "",
        "> 本报告由 Change Analysis Skill 自动生成，属于证据报告；业务分析、风险结论与测试用例尚待分析确认，不代表已执行测试。", "",
        "## 分析范围", "",
        f"- 模式：{'Mode B（需求对照）' if mode_b else 'Mode A（仅代码变更）'}",
        f"- 变更来源：{_value(context.get('source') or ('patch' if request.get('diff_file') else impact.get('source')))}",
        "- 覆盖范围：仅记录已采集的变更文件及 AST 证据；已深入检查的业务模块待分析确认。",
        "- 未覆盖：候选引用未逐一核实；未读取、截断或无法验证的内容见待确认与限制。", "",
        "## 仓库上下文", "",
        f"- 分析模式：{'Diff-only analysis' if diff_only else analysis_mode}",
        f"- 仓库来源：{repository_source}",
        f"- PR 编号：{_value(context.get('pr_number') or repository.get('pr_number'))}",
        f"- base SHA：{_value(context.get('base_sha') or context.get('base_revision') or repository.get('base_commit') or impact.get('base_revision'))}",
        f"- head SHA：{_value(context.get('head_sha') or context.get('target_revision') or repository.get('target_commit'))}",
        f"- merge base：{_value(context.get('merge_base') or repository.get('merge_base'))}",
        f"- PR 信息来源：{_value(context.get('pr_info_source') or repository.get('pr_info_source'))}",
        "- 完整搜索调用链：否；候选引用不等于已确认调用关系。",
        f"- 候选源码文件 / 已扫描源码文件：{_value(scan.get('candidate_source_files'))} / {_value(scan.get('scanned_source_files'))}（未提供时不以变更文件数代替）",
        f"- 轻量级调用图：定义数 {_value(quality.get('definitions'))} / 调用点数 {_value(quality.get('ast_calls_from_changed_scopes'))} / 调用边数 {_value(quality.get('confirmed_edges'))}",
        f"- 全项目源码扫描完成：{'是' if full_scan else '否；仅变更范围证据或缺少完整扫描证明，不能视为全仓库扫描。'}",
        f"- 未解析的 Symbol：{_cell(impact.get('unresolved_symbols') or quality.get('unresolved_symbols', '未提供'))}",
        "- 未深入检查的文件：候选引用及变更范围外文件未确认，详见补充证据。", "",
    ]
    if diff_only:
        lines.extend(["未获取完整仓库，无法确认所有间接调用方和完整回归范围。", ""])
    lines.extend([
        "## 改动与需求摘要", "",
        f"- 代码事实：采集到 {len(changed_files)} 个变更文件、{len(changed_symbols)} 个变更符号；具体行为变化待分析，文件/符号清单见补充证据。",
        f"- 需求来源：{_cell(title)}",
        ("- 需求结论：已获取需求正文，但需求与实现匹配情况待分析。" if mode_b else "- 需求推断：待分析，置信度未评估；未获取可用需求正文，不能确认验收预期。"), "",
        "## 影响链路", "",
        _table(["入口/场景", "前端", "接口", "后端", "数据/外部依赖", "影响类型", "证据"],
               [(pending, "待分析", "待分析", "待分析", "待分析", "待确认", "见补充证据；不将候选关系当作直接或间接回归结论")]), "",
        "## 调用链影响", "",
        _table(["变更对象", "直接调用方", "间接业务场景", "关联接口", "关联数据", "关联测试", "证据"],
               [("待分析", "待确认", "待分析", "待分析", "待分析", "待核实候选测试", "文本引用、推断链路与已确认调用关系不可混同；公共方法/组件置信度待评估")]), "",
        "## 风险结论", "",
        _table(["风险", "等级", "原因", "缓解/验证方式"],
               [("待分析（非无风险）", "待确认", "规则命中仅为风险候选，不是确认结论", "核实源码、需求与候选证据后评估")]), "",
        "## 必测内容", "",
        _table(["ID", "优先级", "类型", "前置条件/数据", "操作", "预期结果", "证据"],
               [("待分析", "待确认", "待设计", "待确认", "待设计", "待确认，不虚构验收结果", "自动证据尚未形成已确认用例")]), "",
        "## 回归范围", "",
        "- 核心回归：待分析；共享入口、状态、接口或数据关系尚未确认。",
        "- 定向回归：待分析；具体消费者和相邻边界需源码核实。",
        "- 可不回归：未确认；缺少明确隔离证据，不做排除承诺。", "",
    ])
    if mode_b:
        lines.extend([
            "## 需求追踪（仅 Mode B）", "",
            _table(["需求 ID", "验收点", "实现证据", "测试证据/建议", "状态"],
                   [("待提取", "待分析", "待对照补充证据", "待设计；未执行", "待分析")]), "",
            "### 疑似超范围实现", "", "待分析：尚未完成需求与实现对照，不能断言存在或不存在超范围实现。", "",
        ])
    lines.extend(["## 建议执行", "",
                  "未执行，仅建议：先核实候选测试文件是否存在及其适用范围，再确认可运行命令；本报告不生成未经仓库验证的命令。", "",
                  "候选测试（尚未核实）：", "", _items(related_tests), "",
                  "## 待确认与限制", "",
                  "- 待确认：业务入口、需求预期、影响类型、风险等级、测试数据与发布决策均待分析。",
                  ("- 需求限制：需求正文已获取，但验收点及实现匹配尚待分析。" if mode_b else "- 需求缺口：未获取可用需求正文；仅 URL、标题或空白内容不构成需求对照，按 Mode A 输出。"),
                  f"- 分析限制：diff 截断：{_value(diff_truncated, '未提供')}；动态配置和外部系统未确认。",
                  "- 需求获取状态（部分失败或截断不代表完整需求）：",
                  _table(["文档", "状态", "错误", "元数据（含截断信息）"], [
                      (document.get("url") or (document.get("meta") or {}).get("title"),
                       document.get("status", "未提供"), document.get("error"), document.get("meta"))
                      for document in documents
                  ]),
                  _items(impact.get("analysis_limits") or []), _items(result.get("evidence_warnings") or []), "",
                  "<details>", "<summary>补充证据（技术明细、selection/impact_units、risk_scan 与影响图）</summary>", "",
                  "### 证据元数据", "",
                  _table(["字段", "值"], [("需求 URL", request.get("requirement_url")),
                        ("仓库", request.get("repository") or repository.get("repository")),
                        ("目标分支", request.get("branch") or repository.get("target_branch")),
                        ("基准分支", request.get("base_branch") or repository.get("base_branch")),
                        ("effort", result.get("effort") or (result.get("analysis_input") or {}).get("effort")),
                        ("Skill 状态", result.get("status")), ("生成日期", generated_on),
                        ("完整证据 JSON", request.get("analysis_output") or "stdout / 内存结果；/repository"),
                        ("分析输入", result.get("analysis_input_output") or "结果中的 analysis_input"),
                        ("Git 证据范围", context.get("source") or impact.get("range")),
                        ("AST 扫描范围", scan.get("ast_scan_scope") or impact.get("analysis_scope")),
                        ("全仓库 AST 扫描标记（非完整调用链证明）", scan.get("whole_repository_ast_scan"))]), "",
                  "### 需求正文（来源证据，未作匹配结论）", ""])
    # Quote source text so requirement headings cannot alter the report structure.
    lines.extend(["\n".join("> " + line for line in requirement_content.splitlines()) if mode_b else "未获取可用需求正文。", "",
                  "### selection_plan 与 impact_units", "",
                  _table(["文件", "状态", "AST 范围数", "候选引用数", "原因"],
                         [(item.get("path"), item.get("status"), item.get("ast_scope_count"), item.get("candidate_reference_count"), "; ".join(item.get("reasons") or [])) for item in selection_files]), "",
                  _table(["单元", "标签", "文件数", "Diff 字节数", "指南章节 ID（不内嵌指南）"],
                         [(item.get("id"), item.get("label"), len(item.get("files") or []), item.get("diff_byte_estimate"), ", ".join(item.get("applicable_guide_sections") or [])) for item in units]), "",
                  "### 变更文件与符号", "",
                  _table(["文件", "变更类型", "新增行", "删除行"], [(item.get("path"), item.get("change_type") or item.get("status"), item.get("added_lines"), item.get("deleted_lines")) for item in changed_files]), "",
                  _table(["符号", "类型", "文件", "行号", "变更类型"], [(item.get("qualified_name") or item.get("symbol"), item.get("kind"), item.get("file"), item.get("line"), item.get("change_type")) for item in changed_symbols]), "",
                  "### Java AST 与图质量", "",
                  _table(["文件", "解析器", "范围", "变更范围数", "解析错误数"], [(item.get("path"), item.get("parser"), item.get("analysis_scope"), len(item.get("changed_scopes") or []), len(item.get("parse_errors") or [])) for item in (impact.get("java_ast") or {}).get("files") or []]), "",
                  _table(["指标", "值"], list(quality.items()) + list(scan.items())), "",
                  "### 候选引用与依赖（未确认调用关系）", ""])
    for label, key in [("符号引用", "symbol_references"), ("数据库依赖", "database_dependencies"), ("前端消费者", "frontend_consumers"), ("后端调用候选", "backend_callers"), ("关联测试", "related_tests")]:
        lines.extend([f"#### {label}", "", _items(impact.get(key) or []), ""])
    risk_scan = repository.get("risk_scan")
    if isinstance(risk_scan, dict):
        lines.extend(["### risk_scan 确定性风险候选", "",
                      "规则命中需人工确认；无命中不代表正确或无风险。", "",
                      _table(["规则", "严重性（规则）", "文件", "行号", "审查提示"],
                             [(f.get("rule_id"), f.get("severity"), f.get("file"), f.get("line"), f.get("message")) for f in risk_scan.get("findings") or []]), "",
                      "规则排除文件：" + ", ".join(risk_scan.get("excluded_files") or []), ""])
    try:
        from .archify_report import build_archify_graph, graph_to_mermaid

        graph = build_archify_graph(result)
        if len(graph.get("nodes", [])) > 1:
            lines.extend(["### 影响图（Mermaid）", "",
                          "需求 → ImpactUnit → 变更文件 → 候选引用；红色虚线为未确认关系，仍需源码验证。", ""])
            if (result.get("archify_report") or {}).get("path"):
                lines.extend(["交互式 HTML 图：`change-analysis-report.html`（浏览器支持缩放、拖动与点击）。", ""])
            lines.extend(["```mermaid", graph_to_mermaid(graph), "```", ""])
    except Exception:
        lines.extend(["影响图未能生成；不影响其余证据，图关系仍待确认。", ""])
    lines.extend(["</details>", "", f"报告生成日期：{generated_on}。", ""])
    return "\n".join(lines)
