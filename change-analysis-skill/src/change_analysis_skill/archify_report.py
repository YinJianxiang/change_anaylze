"""Render Skill evidence as a portable interactive HTML graph (archify-style).

This is a *presentation* layer only. It consumes evidence the Skill already
produced (``selection_plan``, ``impact_units``, ``repository_impact``,
``uncertainties``) and turns it into an explorable graph so reviewers can see
the change blast radius and -- critically -- which nodes are still unconfirmed.

It does NOT add analysis logic and must never change how evidence is collected.
Candidate references and CoverageReflector uncertainties are rendered in red /
dashed to preserve the project rule that they are not confirmed call edges.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .report import requirement_directory_name

_FILE_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "included": ("#2e7d32", "#1b5e20"),
    "non_java_light": ("#1565c0", "#0d47a1"),
    "truncated": ("#ef6c00", "#e65100"),
    "summary_only": ("#757575", "#424242"),
    "evidence_failed": ("#c62828", "#7f0000"),
}

_CANDIDATE_POOL_KEYS = (
    "symbol_references",
    "backend_callers",
    "related_tests",
    "database_dependencies",
    "caller_candidates",
)


def _requirement_title(result: dict[str, Any]) -> str:
    documents = result.get("requirement_documents") or []
    for document in documents:
        meta = document.get("meta") or {}
        title = meta.get("title")
        if title:
            return str(title)
    url = (result.get("request") or {}).get("requirement_url") or "requirement"
    return str(url)


def _candidate_label(item: Any) -> str:
    if isinstance(item, dict):
        return str(
            item.get("symbol")
            or item.get("qualified_name")
            or item.get("name")
            or item.get("path")
            or item.get("file")
            or item
        )
    return str(item)


def _candidate_node_id(item: Any) -> str:
    return "candidate::" + _candidate_label(item)


def _collect_uncertainties(result: dict[str, Any]) -> list[str]:
    items: list[str] = []
    analysis = result.get("result") if isinstance(result.get("result"), dict) else result.get("analysis")
    source = analysis if isinstance(analysis, dict) else result
    for value in source.get("uncertainties") or []:
        items.append(str(value))
    coverage = result.get("coverage_reflection") or (analysis or {}).get("coverage_reflection")
    if isinstance(coverage, dict):
        for gap in coverage.get("gaps") or []:
            items.append(str(gap))
    return items


def _file_status(selection: dict[str, Any], path: str) -> str:
    plans = selection.get("repositories") if isinstance(selection, dict) and "repositories" in selection else [selection]
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        for item in plan.get("files") or []:
            if str(item.get("path")) == path:
                return str(item.get("status") or "included")
    return "included"


def build_archify_graph(result: dict[str, Any]) -> dict[str, Any]:
    """Turn Skill evidence into a node/edge graph for interactive rendering."""
    repository = result.get("repository") or {}
    impact = repository.get("repository_impact") or {}
    selection = result.get("selection_plan") or (result.get("analysis_input") or {}).get("selection_plan") or {}
    impact_units = result.get("impact_units") or (result.get("analysis_input") or {}).get("impact_units") or []

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    req_id = "requirement"
    nodes.append({
        "id": req_id,
        "label": _requirement_title(result),
        "group": "requirement",
        "color": {"background": "#1e88e5", "border": "#0d47a1"},
    })

    def ensure_node(node: dict[str, Any]) -> None:
        if node["id"] in node_ids:
            return
        node_ids.add(node["id"])
        nodes.append(node)

    # Impact units -> files -> candidate references.
    for unit in impact_units:
        uid = str(unit.get("id") or f"unit-{len(node_ids)}")
        ensure_node({
            "id": uid,
            "label": str(unit.get("label") or uid),
            "group": "unit",
            "color": {"background": "#7e57c2", "border": "#4527a0"},
            "detail": {
                "files": unit.get("files") or [],
                "diff_bytes": unit.get("diff_byte_estimate"),
                "guide_sections": unit.get("applicable_guide_sections") or [],
            },
        })
        edges.append({"from": req_id, "to": uid, "kind": "scope", "dashed": False})
        for path in unit.get("files") or []:
            fid = "file::" + str(path)
            status = _file_status(selection, str(path))
            bg, border = _FILE_STATUS_COLORS.get(status, _FILE_STATUS_COLORS["included"])
            ensure_node({
                "id": fid,
                "label": str(path),
                "group": "file",
                "status": status,
                "color": {"background": bg, "border": border},
            })
            edges.append({"from": uid, "to": fid, "kind": "unit-file", "dashed": False})
            for candidate in unit.get("related_candidates") or []:
                cid = _candidate_node_id(candidate)
                ensure_node({
                    "id": cid,
                    "label": _candidate_label(candidate),
                    "group": "candidate",
                    "color": {"background": "#ffebee", "border": "#c62828"},
                })
                edges.append({"from": fid, "to": cid, "kind": "candidate", "dashed": True})

    # Changed symbols (purple) linked to their file.
    for symbol in impact.get("changed_symbols") or []:
        if not isinstance(symbol, dict):
            continue
        sid = "symbol::" + str(symbol.get("qualified_name") or symbol.get("symbol") or symbol)
        ensure_node({
            "id": sid,
            "label": str(symbol.get("qualified_name") or symbol.get("symbol") or symbol),
            "group": "symbol",
            "color": {"background": "#e1bee7", "border": "#8e24aa"},
        })
        fid = "file::" + str(symbol.get("file") or "")
        if fid in node_ids:
            edges.append({"from": fid, "to": sid, "kind": "symbol", "dashed": False})

    # Top-level candidate pool (deduped by label so 30 identical refs == 1 node).
    for key in _CANDIDATE_POOL_KEYS:
        values = impact.get(key)
        if key == "caller_candidates" and not values:
            slice_value = impact.get("impact_slice")
            if isinstance(slice_value, dict):
                values = slice_value.get("caller_candidates")
        for item in values or []:
            cid = _candidate_node_id(item)
            ensure_node({
                "id": cid,
                "label": _candidate_label(item),
                "group": "candidate",
                "color": {"background": "#ffebee", "border": "#c62828"},
            })
            edges.append({"from": req_id, "to": cid, "kind": "candidate-pool", "dashed": True})

    # Selection files not covered by any unit yet.
    plans = selection.get("repositories") if isinstance(selection, dict) and "repositories" in selection else [selection]
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        for item in plan.get("files") or []:
            path = str(item.get("path") or "")
            if not path:
                continue
            fid = "file::" + path
            if fid in node_ids:
                continue
            status = str(item.get("status") or "included")
            bg, border = _FILE_STATUS_COLORS.get(status, _FILE_STATUS_COLORS["included"])
            ensure_node({
                "id": fid,
                "label": path,
                "group": "file",
                "status": status,
                "color": {"background": bg, "border": border},
            })
            edges.append({"from": req_id, "to": fid, "kind": "file", "dashed": False})

    # Uncertainties from CoverageReflector / analysis result.
    for index, uncertainty in enumerate(_collect_uncertainties(result)):
        nid = f"uncertainty::{index}"
        ensure_node({
            "id": nid,
            "label": uncertainty,
            "group": "uncertainty",
            "color": {"background": "#b71c1c", "border": "#7f0000"},
        })
        edges.append({"from": req_id, "to": nid, "kind": "uncertainty", "dashed": True})

    return {"nodes": nodes, "edges": edges}


def build_archify_meta(result: dict[str, Any]) -> dict[str, Any]:
    request = result.get("request") or {}
    repository = result.get("repository") or {}
    impact = repository.get("repository_impact") or {}
    selection = result.get("selection_plan") or (result.get("analysis_input") or {}).get("selection_plan") or {}
    impact_units = result.get("impact_units") or (result.get("analysis_input") or {}).get("impact_units") or []
    selection_summary = selection.get("summary") if isinstance(selection, dict) else None

    return {
        "title": _requirement_title(result),
        "requirement_url": request.get("requirement_url"),
        "repository": request.get("repository") or repository.get("repository"),
        "target_branch": request.get("branch") or repository.get("target_branch"),
        "base_branch": request.get("base_branch") or repository.get("base_branch"),
        "merge_base": repository.get("merge_base"),
        "target_commit": repository.get("target_commit"),
        "analysis_mode": result.get("analysis_mode") or repository.get("analysis_mode"),
        "effort": result.get("effort") or (result.get("analysis_input") or {}).get("effort"),
        "status": result.get("status"),
        "generated_on": date.today().isoformat(),
        "impact_unit_count": len(impact_units),
        "selection_summary": selection_summary,
        "analysis_limits": impact.get("analysis_limits") or [],
        "evidence_warnings": result.get("evidence_warnings") or [],
        "uncertainties": _collect_uncertainties(result),
    }


def default_archify_path(result: dict[str, Any], reporter_dir: Path) -> Path:
    """Build reporter/<requirement>/change-analysis-report.html."""
    return reporter_dir / requirement_directory_name(result) / "change-analysis-report.html"


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Code Change Impact Graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  :root { --bg:#0f1419; --panel:#161b22; --border:#30363d; --text:#e6edf3; --muted:#8b949e; --accent:#1f6feb; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, sans-serif; background:var(--bg); color:var(--text); }
  header { padding:12px 18px; border-bottom:1px solid var(--border); display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  header .mode { color:var(--muted); font-size:12px; }
  .layout { display:flex; height:calc(100vh - 52px); }
  #graph { flex:1 1 auto; min-width:0; }
  aside { width:340px; flex:0 0 340px; border-left:1px solid var(--border); background:var(--panel); overflow-y:auto; padding:14px; }
  aside h2 { font-size:13px; margin:18px 0 6px; color:var(--accent); text-transform:uppercase; letter-spacing:.04em; }
  aside h2:first-child { margin-top:0; }
  aside table { width:100%; border-collapse:collapse; font-size:12px; }
  aside td { padding:3px 6px; border-bottom:1px solid var(--border); vertical-align:top; }
  aside td.k { color:var(--muted); white-space:nowrap; width:42%; }
  .pill { display:inline-block; padding:1px 7px; border-radius:10px; font-size:11px; margin:1px 2px; }
  .pill.red { background:#b71c1c; color:#fff; }
  .pill.amber { background:#ef6c00; color:#fff; }
  .pill.gray { background:#30363d; color:var(--text); }
  .legend span { display:inline-flex; align-items:center; gap:5px; margin-right:12px; font-size:12px; }
  .swatch { width:12px; height:12px; border-radius:3px; display:inline-block; }
  #detail { font-size:12px; white-space:pre-wrap; word-break:break-all; color:var(--muted); }
  #fallback { display:none; padding:18px; }
  .note { font-size:11px; color:var(--muted); margin-top:6px; }
</style>
</head>
<body>
<header>
  <h1 id="title">Code Change Impact Graph</h1>
  <span class="mode" id="mode"></span>
</header>
<div class="layout">
  <div id="graph"></div>
  <aside>
    <h2>Metadata</h2>
    <table id="meta"></table>

    <h2>Legend</h2>
    <div class="legend">
      <span><i class="swatch" style="background:#1e88e5"></i>需求</span>
      <span><i class="swatch" style="background:#7e57c2"></i>ImpactUnit</span>
      <span><i class="swatch" style="background:#2e7d32"></i>变更文件</span>
      <span><i class="swatch" style="background:#e1bee7"></i>变更符号</span>
      <span><i class="swatch" style="background:#ffebee;border:1px solid #c62828"></i>待确认引用</span>
      <span><i class="swatch" style="background:#b71c1c"></i>不确定性</span>
    </div>
    <div class="note">红色虚线 = 待确认（候选引用 / CoverageReflector 缺口）。这些不是已确认调用边，需结合源码确认。</div>

    <h2>Uncertainties (待确认)</h2>
    <div id="uncertainties"></div>

    <h2>Analysis limits</h2>
    <div id="limits"></div>

    <h2>Evidence warnings</h2>
    <div id="warnings"></div>

    <h2>Selected node</h2>
    <div id="detail">点击图中节点查看详情。</div>
  </aside>
</div>
<div id="fallback">
  <h2>Interactive graph unavailable (offline?)</h2>
  <p>The interactive view needs the vis-network library from a CDN. Below is the same evidence as a static outline.</p>
  <pre id="fallback-data"></pre>
</div>

<script>
/*__DATA__*/
(function () {
  document.getElementById('title').textContent = __DATA__.meta.title || 'Code Change Impact Graph';
  document.getElementById('mode').textContent =
    [__DATA__.meta.analysis_mode, __DATA__.meta.effort ? ('effort=' + __DATA__.meta.effort) : null]
      .filter(Boolean).join('  ·  ');

  const meta = __DATA__.meta;
  const metaRows = [
    ['Requirement', meta.requirement_url],
    ['Repository', meta.repository],
    ['Target branch', meta.target_branch],
    ['Base branch', meta.base_branch],
    ['Merge base', meta.merge_base],
    ['Target commit', meta.target_commit],
    ['Analysis mode', meta.analysis_mode],
    ['Status', meta.status],
    ['Impact units', meta.impact_unit_count],
    ['Generated on', meta.generated_on],
  ];
  if (meta.selection_summary) {
    const s = meta.selection_summary;
    metaRows.push(['Files included', s.included]);
    metaRows.push(['Files light/trunc/fail', (s.non_java_light||0) + '/' + (s.truncated||0) + '/' + (s.evidence_failed||0)]);
  }
  document.getElementById('meta').innerHTML = metaRows
    .map(function (r) { return '<tr><td class="k">' + (r[0]||'-') + '</td><td>' + (r[1] == null ? '-' : String(r[1])) + '</td></tr>'; })
    .join('');

  function pills(list, cls) {
    if (!list || !list.length) return '<span class="pill gray">none</span>';
    return list.slice(0, 40).map(function (x) { return '<span class="pill ' + cls + '">' + String(x).replace(/</g,'&lt;') + '</span>'; }).join('');
  }
  document.getElementById('uncertainties').innerHTML = pills(meta.uncertainties, 'red');
  document.getElementById('limits').innerHTML = pills(meta.analysis_limits, 'amber');
  document.getElementById('warnings').innerHTML = pills(meta.evidence_warnings, 'gray');

  const graph = __DATA__.graph;
  const fallback = document.getElementById('fallback');
  const fallbackData = document.getElementById('fallback-data');
  fallbackData.textContent = JSON.stringify({ meta: meta, nodes: graph.nodes.map(function(n){return n.label;}) }, null, 2);

  if (typeof vis === 'undefined') {
    document.getElementById('graph').style.display = 'none';
    fallback.style.display = 'block';
    return;
  }

  const nodes = new vis.DataSet(graph.nodes.map(function (n) {
    return {
      id: n.id, label: n.label, group: n.group, color: n.color,
      font: { color: (n.group === 'candidate' || n.group === 'uncertainty') ? '#b71c1c' : '#ffffff', size: 13 },
      title: (n.detail ? JSON.stringify(n.detail, null, 2) : n.label)
    };
  }));
  const edges = new vis.DataSet(graph.edges.map(function (e) {
    return {
      from: e.from, to: e.to, dashes: !!e.dashed,
      color: { color: e.dashed ? '#c62828' : '#5b6670', opacity: e.dashed ? 0.9 : 0.5 },
      arrows: 'to'
    };
  }));

  const container = document.getElementById('graph');
  const network = new vis.Network(container, { nodes: nodes, edges: edges }, {
    layout: { improvedLayout: true },
    physics: { stabilization: true, barnesHut: { gravitationalConstant: -8000, springLength: 140 } },
    interaction: { hover: true, tooltipDelay: 120 },
    nodes: { shape: 'box', margin: 6, borderWidth: 1 },
  });

  const detail = document.getElementById('detail');
  network.on('click', function (params) {
    if (!params.nodes.length) { detail.textContent = '点击图中节点查看详情。'; return; }
    const node = nodes.get(params.nodes[0]);
    if (!node) return;
    let text = '[' + node.group + '] ' + node.label;
    if (node.title && node.title !== node.label) text += '\\n\\n' + node.title;
    detail.textContent = text;
  });
})();
</script>
</body>
</html>
"""


_MERMAID_CLASSDEFS = {
    "requirement": "fill:#1e88e5,color:#fff",
    "unit": "fill:#7e57c2,color:#fff",
    "symbol": "fill:#e1bee7,color:#4a148c",
    "candidate": "fill:#ffebee,stroke:#c62828,color:#b71c1c",
    "uncertainty": "fill:#b71c1c,color:#fff",
    "fileOk": "fill:#2e7d32,color:#fff",
    "fileWarn": "fill:#ef6c00,color:#fff",
    "fileFail": "fill:#c62828,color:#fff",
}

_FILE_STATUS_CLASS = {
    "included": "fileOk",
    "non_java_light": "fileOk",
    "summary_only": "fileOk",
    "truncated": "fileWarn",
    "evidence_failed": "fileFail",
}


def graph_to_mermaid(graph: dict[str, Any]) -> str:
    """Render the same impact graph as an inline Mermaid flowchart.

    Reuses ``build_archify_graph`` so the Markdown diagram matches the
    interactive HTML. Red / dashed edges mark unconfirmed evidence.
    """
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for node in graph.get("nodes", []):
        raw = str(node.get("id", ""))
        base = re.sub(r"[^0-9A-Za-z_]", "_", raw) or "n"
        safe = base
        counter = 1
        while safe in used:
            counter += 1
            safe = f"{base}_{counter}"
        used.add(safe)
        id_map[raw] = safe

    lines = ["flowchart LR"]
    for key, style in _MERMAID_CLASSDEFS.items():
        lines.append(f"    classDef {key} {style};")
    for node in graph.get("nodes", []):
        safe = id_map[str(node.get("id"))]
        label = str(node.get("label", "")).replace('"', "'")[:60]
        group = node.get("group")
        if group == "file":
            cls = _FILE_STATUS_CLASS.get(node.get("status"), "fileOk")
        else:
            cls = group if group in _MERMAID_CLASSDEFS else "requirement"
        lines.append(f'    {safe}["{label}"]')
        lines.append(f"    class {safe} {cls};")
    for edge in graph.get("edges", []):
        source = id_map.get(str(edge.get("from")))
        target = id_map.get(str(edge.get("to")))
        if not source or not target:
            continue
        arrow = "-.->" if edge.get("dashed") else "-->"
        lines.append(f"    {source} {arrow} {target}")
    return "\n".join(lines)


def render_archify_html(result: dict[str, Any]) -> str:
    """Return a self-contained interactive HTML string for the Skill result."""
    payload = {
        "meta": build_archify_meta(result),
        "graph": build_archify_graph(result),
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _HTML_TEMPLATE.replace("/*__DATA__*/", "const __DATA__ = " + data_json + ";")
