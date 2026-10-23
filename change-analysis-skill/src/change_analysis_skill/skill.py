"""One-shot requirement-aware Git change analysis, independent of the legacy app."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis_context import codex_snapshots, enrich_snapshots
from .git_service import GitService
from .guidance import load_analysis_guidance
from .requirement_service import RequirementService
from .archify_report import default_archify_path, render_archify_html
from .report import default_report_path, render_markdown
from .selection import build_analysis_planning, format_preview
from .risk_scan import gate_failed, scan_diff


class SkillInputError(ValueError):
    """Raised when a direct skill request is incomplete or invalid."""


@dataclass(frozen=True)
class ChangeAnalysisRequest:
    requirement_url: str
    branch: str
    repository: Path
    base_branch: str = "master"
    repository_name: str = "repository"
    risk_exclude: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.requirement_url.strip():
            raise SkillInputError("requirement_url must not be empty")
        if not self.branch.strip():
            raise SkillInputError("branch must not be empty")
        if not self.base_branch.strip():
            raise SkillInputError("base_branch must not be empty")
        if not self.repository.exists():
            raise SkillInputError(f"repository path does not exist: {self.repository}")
        if not (self.repository / ".git").exists():
            raise SkillInputError(f"repository path is not a Git checkout: {self.repository}")


class ChangeAnalysisSkill:
    """Fetch requirements and analyze Git changes without mail, bots, or workers."""

    def __init__(
        self,
        *,
        requirement_service: RequirementService | None = None,
        git_service: GitService | None = None,
        evidence_timeout: int = 60,
        max_diff_bytes: int = 0,
    ) -> None:
        self.requirement_service = requirement_service or RequirementService()
        self.git_service = git_service or GitService()
        self.evidence_timeout = evidence_timeout
        self.max_diff_bytes = max_diff_bytes

    def run(self, request: ChangeAnalysisRequest) -> dict[str, Any]:
        request.validate()
        snapshot = self.git_service.snapshot_repository(
            repository=request.repository,
            branch=request.branch,
            base_branch=request.base_branch,
            repository_name=request.repository_name,
            fetch_remote=True,
        )
        requirement_documents = self.requirement_service.prepare([request.requirement_url]).get("documents", [])
        if request.risk_exclude:
            snapshot = {**snapshot, "risk_exclude": list(request.risk_exclude)}
        if not isinstance(requirement_documents, list):
            raise SkillInputError("requirement service returned an invalid documents value")
        snapshots, analysis_mode, evidence_warnings = enrich_snapshots(
            [snapshot], timeout=self.evidence_timeout
        )
        summary_snapshots = codex_snapshots(snapshots, max_diff_bytes=self.max_diff_bytes)
        planning = build_analysis_planning(summary_snapshots)
        summary_warnings = list(evidence_warnings)
        if summary_snapshots[0].get("diff_truncated_for_analysis"):
            summary_warnings.append("Summary diff was truncated; read the full diff via evidence_reference.")
        analysis_input: dict[str, Any] = {
            "analysis_mode": analysis_mode,
            "analysis_goal": "requirement-aware code change analysis",
            "analysis_guidance": load_analysis_guidance(),
            "requirement_urls": [request.requirement_url],
            "requirement_documents": requirement_documents,
            "repositories": summary_snapshots,
            "evidence_warnings": summary_warnings,
            "evidence_reference": {"json_pointer": "/repository"},
            "effort": planning["effort"],
            "selection_plan": planning["selection_plan"],
            "impact_units": planning["impact_units"],
        }
        return {
            "request": {
                "requirement_url": request.requirement_url,
                "repository": str(request.repository),
                "base_branch": request.base_branch,
                "branch": request.branch,
            },
            "requirement_documents": requirement_documents,
            "repository": snapshots[0],
            "analysis_mode": analysis_mode,
            "evidence_warnings": evidence_warnings,
            "effort": planning["effort"],
            "selection_plan": planning["selection_plan"],
            "impact_units": planning["impact_units"],
            "status": "READY_FOR_CODEX",
            "analysis_input": analysis_input,
            "analysis": None,
        }


def prepare_patch(path: Path, exclude: tuple[str, ...] = (), max_diff_bytes: int = 0) -> dict[str, Any]:
    """Build the normal handoff envelope without network or repository access."""
    diff = path.read_text(encoding="utf-8-sig")
    if not diff.strip() or not any(line.startswith(("diff --git ", "--- ")) for line in diff.splitlines()):
        raise SkillInputError("Expected a nonempty unified diff or Git patch")
    scan = scan_diff(diff, exclude=exclude)
    warning = "Offline patch: requirement, Git revisions, AST and caller evidence were not collected."
    snapshot = {"project": path.stem, "diff": diff, "changed_files": scan["scanned_files"] + scan["excluded_files"],
                "risk_scan": scan, "change_context": None, "repository_impact": None,
                "analysis_mode": "Diff-only evidence analysis", "evidence_warnings": [warning]}
    summaries = codex_snapshots([snapshot], max_diff_bytes=max_diff_bytes)
    planning = build_analysis_planning(summaries)
    analysis_input = {"analysis_mode": snapshot["analysis_mode"], "analysis_goal": "offline patch analysis",
                      "analysis_guidance": load_analysis_guidance(),
                      "requirement_urls": [], "requirement_documents": [], "repositories": summaries,
                      "evidence_warnings": [warning], "evidence_reference": {"json_pointer": "/repository"}, **planning}
    return {"request": {"diff_file": str(path.resolve())}, "repository": snapshot,
            "requirement_documents": [], "analysis_mode": snapshot["analysis_mode"],
            "evidence_warnings": [warning], "analysis_input": analysis_input,
            "analysis": None, "status": "READY_FOR_CODEX", **planning}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SkillInputError(f"Invalid .env entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise SkillInputError(f"Invalid environment variable name at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _repository_path(repo: Path | None, name: str, env_file: Path) -> Path:
    """CLI paths use cwd; configured paths are relative to the selected env file."""
    if repo is not None:
        return repo.resolve()
    if name == "repository":
        value = os.environ.get("SKILL_REPO", "").strip()
        if not value:
            raise SkillInputError(f"Repository path is required: pass --repo or configure SKILL_REPO in {env_file}")
    else:
        try:
            repositories = json.loads(os.environ.get("SKILL_REPOSITORIES", "{}"))
        except json.JSONDecodeError:
            raise SkillInputError("SKILL_REPOSITORIES must be a JSON object mapping repository names to paths") from None
        if not isinstance(repositories, dict) or any(
            not isinstance(path, str) or not path.strip() for path in repositories.values()
        ):
            raise SkillInputError("SKILL_REPOSITORIES must be a JSON object mapping repository names to nonempty paths")
        value = repositories.get(name, "").strip()
        if not value:
            raise SkillInputError(f"Repository {name!r} is not configured: pass --repo or add it to SKILL_REPOSITORIES in {env_file}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = env_file.resolve().parent / path
    return path.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a TAPD/DingTalk requirement and prepare Git change evidence for Codex analysis."
    )
    parser.add_argument("requirement_url", nargs="?", help="TAPD story URL or DingTalk document URL")
    parser.add_argument("branch", nargs="?", help="Target branch to analyze")
    parser.add_argument("--diff-file", type=Path, help="Offline unified patch; no Git, requirement fetch or AST collection")
    parser.add_argument("--risk-exclude", nargs="+", default=[], help="Exact directory segments excluded from risk rules only; full evidence is preserved")
    parser.add_argument("--fail-on", choices=("low", "medium", "high"), help="Opt-in CI gate: exit 1 when a rule candidate reaches this severity")
    parser.add_argument("--repo", type=Path, help="Local Git checkout; overrides SKILL_REPO or SKILL_REPOSITORIES")
    parser.add_argument("--base-branch", help="Comparison base branch (SKILL_BASE_BRANCH or master)")
    parser.add_argument("--repository-name", default="repository", help="Repository name used to select a path from SKILL_REPOSITORIES and label the report")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--evidence-timeout", type=int, default=60)
    parser.add_argument("--max-diff-bytes", type=int, default=0, help="Limit the summary diff only; the full evidence retains the complete snapshot diff")
    parser.add_argument("--output", type=Path, help="Write full evidence JSON here and a sibling <stem>.analysis-input.json summary")
    parser.add_argument("--report-output", type=Path, help="Write the Markdown report to this exact file; overrides --reporter-dir")
    parser.add_argument("--reporter-dir", type=Path, default=Path("reporter"), help="Root directory for automatic reporter/<requirement>/change-analysis-report.md output")
    parser.add_argument("--no-report", action="store_true", help="Do not write a Markdown report")
    parser.add_argument(
        "--no-archify",
        action="store_true",
        help="Do not write the interactive HTML impact graph (archify-style); the inline Markdown graph is still included",
    )
    parser.add_argument(
        "--archify-output",
        type=Path,
        help="Write the interactive HTML graph to this exact file; overrides --reporter-dir",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Collect evidence, print selection_plan/impact_units, set status=PREVIEW",
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high"),
        default=None,
        help="Analysis effort hint for the code-analysis Skill (default: ANALYSIS_EFFORT or medium)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _load_env_file(args.env_file)
        if args.effort:
            os.environ["ANALYSIS_EFFORT"] = args.effort
        if args.diff_file:
            if args.requirement_url or args.branch or args.repo:
                raise SkillInputError("--diff-file cannot be combined with requirement_url, branch or --repo")
            result = prepare_patch(args.diff_file, tuple(args.risk_exclude), args.max_diff_bytes)
        else:
            if not args.requirement_url or not args.branch:
                raise SkillInputError("requirement_url and branch are required unless --diff-file is used")
            result = ChangeAnalysisSkill(
                evidence_timeout=args.evidence_timeout,
                max_diff_bytes=args.max_diff_bytes,
            ).run(
                ChangeAnalysisRequest(
                    requirement_url=args.requirement_url,
                    branch=args.branch,
                    repository=_repository_path(args.repo, args.repository_name, args.env_file),
                    base_branch=args.base_branch or os.environ.get("SKILL_BASE_BRANCH", "master"),
                    repository_name=args.repository_name,
                    risk_exclude=tuple(args.risk_exclude),
                )
            )
    except (SkillInputError, OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"change-analysis-skill: {error}\n")

    if args.preview:
        result["status"] = "PREVIEW"

    summary_path = None
    if args.output:
        output_path = args.output.resolve()
        summary_path = output_path.with_name(output_path.stem + ".analysis-input.json")
        result["analysis_input"]["evidence_reference"]["path"] = str(output_path)
        result["request"]["analysis_output"] = str(output_path)
        result["analysis_input_output"] = str(summary_path)

    report_path = None
    if not args.preview and not args.no_report:
        report_path = args.report_output or default_report_path(result, args.reporter_dir)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        result["report"] = {
            "format": "markdown",
            "path": str(report_path),
        }
        report_path.write_text(render_markdown(result), encoding="utf-8")

    archify_path = None
    if not args.preview and not args.no_archify:
        archify_path = args.archify_output or default_archify_path(result, args.reporter_dir)
        archify_path.parent.mkdir(parents=True, exist_ok=True)
        result["archify_report"] = {
            "format": "html",
            "path": str(archify_path),
        }
        archify_path.write_text(render_archify_html(result), encoding="utf-8")

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        summary_path.write_text(
            json.dumps(result["analysis_input"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.preview:
        print(
            format_preview({
                "effort": result.get("effort"),
                "selection_plan": result.get("selection_plan"),
                "impact_units": result.get("impact_units"),
            }),
            end="",
        )
        if not args.output:
            print(rendered)
    elif not args.output:
        print(rendered)
    return 1 if gate_failed(result.get("repository", {}).get("risk_scan", {"findings": []}), args.fail_on) else 0
