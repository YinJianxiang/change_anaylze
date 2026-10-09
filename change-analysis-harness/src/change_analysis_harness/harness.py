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
from .requirement_service import RequirementService
from .report import default_report_path, render_markdown
from .selection import build_analysis_planning, format_preview


class HarnessInputError(ValueError):
    """Raised when a direct harness request is incomplete or invalid."""


@dataclass(frozen=True)
class ChangeAnalysisRequest:
    requirement_url: str
    branch: str
    repository: Path
    base_branch: str = "master"
    repository_name: str = "repository"

    def validate(self) -> None:
        if not self.requirement_url.strip():
            raise HarnessInputError("requirement_url must not be empty")
        if not self.branch.strip():
            raise HarnessInputError("branch must not be empty")
        if not self.base_branch.strip():
            raise HarnessInputError("base_branch must not be empty")
        if not self.repository.exists():
            raise HarnessInputError(f"repository path does not exist: {self.repository}")
        if not (self.repository / ".git").exists():
            raise HarnessInputError(f"repository path is not a Git checkout: {self.repository}")


class ChangeAnalysisHarness:
    """Fetch requirements and analyze Git changes without mail, bots, or workers."""

    def __init__(
        self,
        *,
        requirement_service: RequirementService | None = None,
        git_service: GitService | None = None,
        evidence_timeout: int = 60,
        max_diff_bytes: int = 0,
        fetch_remote: bool = True,
    ) -> None:
        self.requirement_service = requirement_service or RequirementService()
        self.git_service = git_service or GitService()
        self.evidence_timeout = evidence_timeout
        self.max_diff_bytes = max_diff_bytes
        self.fetch_remote = fetch_remote

    def run(self, request: ChangeAnalysisRequest) -> dict[str, Any]:
        request.validate()
        requirement_documents = self.requirement_service.prepare([request.requirement_url]).get("documents", [])
        if not isinstance(requirement_documents, list):
            raise HarnessInputError("requirement service returned an invalid documents value")

        snapshot = self.git_service.snapshot_repository(
            repository=request.repository,
            branch=request.branch,
            base_branch=request.base_branch,
            repository_name=request.repository_name,
            fetch_remote=self.fetch_remote,
        )
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
            raise HarnessInputError(f"Invalid .env entry at {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise HarnessInputError(f"Invalid environment variable name at {path}:{line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a TAPD/DingTalk requirement and prepare Git change evidence for Codex analysis."
    )
    parser.add_argument("requirement_url", help="TAPD story URL or DingTalk document URL")
    parser.add_argument("branch", help="Target branch to analyze")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Local Git checkout")
    parser.add_argument("--base-branch", default="master", help="Comparison base branch")
    parser.add_argument("--repository-name", default="repository", help="Display name in the report")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--evidence-timeout", type=int, default=60)
    parser.add_argument("--max-diff-bytes", type=int, default=0, help="Limit the summary diff only; the full evidence retains the complete snapshot diff")
    parser.add_argument("--output", type=Path, help="Write full evidence JSON here and a sibling <stem>.analysis-input.json summary")
    parser.add_argument("--report-output", type=Path, help="Write the Markdown report to this exact file; overrides --reporter-dir")
    parser.add_argument("--reporter-dir", type=Path, default=Path("reporter"), help="Root directory for automatic reporter/<requirement>/change-analysis-report.md output")
    parser.add_argument("--no-report", action="store_true", help="Do not write a Markdown report")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use existing origin refs without contacting the Git remote",
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
        help="Analysis effort hint for Codex/bot (default: ANALYSIS_EFFORT or medium)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _load_env_file(args.env_file)
        if args.effort:
            os.environ["ANALYSIS_EFFORT"] = args.effort
        result = ChangeAnalysisHarness(
            evidence_timeout=args.evidence_timeout,
            max_diff_bytes=args.max_diff_bytes,
            fetch_remote=not args.no_fetch,
        ).run(
            ChangeAnalysisRequest(
                requirement_url=args.requirement_url,
                branch=args.branch,
                repository=args.repo.resolve(),
                base_branch=args.base_branch,
                repository_name=args.repository_name,
            )
        )
    except (HarnessInputError, OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"change-analysis-harness: {error}\n")

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
    return 0
