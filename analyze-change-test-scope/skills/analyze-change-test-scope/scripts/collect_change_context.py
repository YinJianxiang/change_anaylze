#!/usr/bin/env python3
"""Collect read-only Git change evidence as structured JSON."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True, timeout: int = 60) -> str:
    try:
        process = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {timeout}s") from exc
    if check and process.returncode:
        raise RuntimeError(process.stderr.strip() or f"git {' '.join(args)} failed")
    return process.stdout


def has_head(repo: Path) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect status, file statistics, and unified diff without modifying the repository."
    )
    parser.add_argument("--repo", default=".", help="Git repository path (default: current directory)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--staged", action="store_true", help="Analyze staged changes only")
    source.add_argument("--commit", metavar="REV", help="Analyze one commit")
    source.add_argument("--range", dest="revision_range", metavar="BASE...HEAD", help="Analyze a Git range")
    parser.add_argument(
        "--max-diff-bytes",
        type=int,
        default=400_000,
        help="Maximum UTF-8 bytes retained from the unified diff (default: 400000)",
    )
    parser.add_argument("--pr-number", type=int, help="Optional PR number for report metadata")
    parser.add_argument("--pr-info-source", help="PR metadata source, for example gh or github-api")
    parser.add_argument(
        "--analysis-mode",
        choices=("full", "diff-only"),
        help="Whether a complete repository context is available",
    )
    parser.add_argument("--timeout", type=int, default=60, help="Per-command timeout in seconds")
    return parser.parse_args()


def truncate_utf8(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    return clipped + "\n[diff truncated by collect_change_context.py]\n", True


def resolve_revision_metadata(
    repo: Path, revision_range: str | None, commit: str | None, timeout: int
) -> dict[str, str | None]:
    base_sha: str | None = None
    head_sha: str | None = None
    merge_base: str | None = None
    if revision_range:
        separator = "..." if "..." in revision_range else ".."
        parts = revision_range.split(separator, 1)
        if len(parts) == 2 and all(parts):
            base_sha = git(repo, "rev-parse", parts[0], timeout=timeout).strip()
            head_sha = git(repo, "rev-parse", parts[1], timeout=timeout).strip()
            merge_base = git(repo, "merge-base", base_sha, head_sha, timeout=timeout).strip()
    elif commit:
        head_sha = git(repo, "rev-parse", commit, timeout=timeout).strip()
        parent = git(repo, "rev-parse", f"{commit}^", check=False, timeout=timeout).strip()
        if parent:
            base_sha = parent
            merge_base = parent
    elif has_head(repo):
        head_sha = git(repo, "rev-parse", "HEAD", timeout=timeout).strip()
    return {"base_sha": base_sha, "head_sha": head_sha, "merge_base": merge_base}


def parse_changed_files(name_status: list[str], numstat: list[str]) -> list[dict[str, object]]:
    statistics: dict[str, tuple[int | None, int | None]] = {}
    for line in numstat:
        parts = line.split("\t")
        if len(parts) >= 3:
            added = None if parts[0] == "-" else int(parts[0])
            deleted = None if parts[1] == "-" else int(parts[1])
            statistics[parts[-1]] = (added, deleted)
    status_names = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type-changed", "U": "unmerged"}
    result: list[dict[str, object]] = []
    for line in name_status:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0]
        key = code[:1]
        path = parts[-1]
        added, deleted = statistics.get(path, (None, None))
        item: dict[str, object] = {
            "path": path,
            "change_type": status_names.get(key, "unknown"),
            "status": code,
            "added_lines": added,
            "deleted_lines": deleted,
        }
        if key in {"R", "C"} and len(parts) >= 3:
            item["old_path"] = parts[1]
        result.append(item)
    return result


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    timeout = max(args.timeout, 1)
    try:
        root = Path(git(repo, "rev-parse", "--show-toplevel", timeout=timeout).strip())
        status = git(root, "status", "--short", "--untracked-files=all", timeout=timeout)

        if args.staged:
            source = "staged"
            diff_args = ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--find-renames"]
        elif args.commit:
            source = f"commit:{args.commit}"
            diff_args = [
                "show",
                "--format=",
                "--no-ext-diff",
                "--no-textconv",
                "--find-renames",
                args.commit,
            ]
        elif args.revision_range:
            source = f"range:{args.revision_range}"
            diff_args = [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--find-renames",
                args.revision_range,
            ]
        else:
            source = "working-tree"
            if has_head(root):
                diff_args = ["diff", "HEAD", "--no-ext-diff", "--no-textconv", "--find-renames"]
            else:
                diff_args = ["diff", "--no-ext-diff", "--no-textconv", "--find-renames"]

        if diff_args[0] == "show":
            name_status_args = ["show", "--name-status", *diff_args[1:]]
            numstat_args = ["show", "--numstat", *diff_args[1:]]
        else:
            name_status_args = ["diff", "--name-status", *diff_args[1:]]
            numstat_args = ["diff", "--numstat", *diff_args[1:]]

        unified = git(root, *diff_args, timeout=timeout)
        unified, truncated = truncate_utf8(unified, max(args.max_diff_bytes, 0))
        untracked = [line[3:] for line in status.splitlines() if line.startswith("?? ")]
        name_status = git(root, *name_status_args, timeout=timeout).splitlines()
        numstat = git(root, *numstat_args, timeout=timeout).splitlines()
        revisions = resolve_revision_metadata(root, args.revision_range, args.commit, timeout)
        analysis_mode = (
            "Full repository context analysis"
            if args.analysis_mode == "full"
            else "Diff-only analysis" if args.analysis_mode == "diff-only" else "Full repository context analysis"
        )
        result = {
            "repository": str(root),
            "source": source,
            "analysis_mode": analysis_mode,
            "pr_number": args.pr_number,
            "pr_info_source": args.pr_info_source,
            **revisions,
            "status_short": status.splitlines(),
            "name_status": name_status,
            "numstat": numstat,
            "changed_files": parse_changed_files(name_status, numstat),
            "untracked_files": untracked,
            "diff_truncated": truncated,
            "unified_diff": unified,
            "notes": [
                "Untracked file contents are not included; inspect relevant files separately.",
                "Binary contents and text-conversion filters are intentionally excluded.",
            ],
        }
        if analysis_mode == "Diff-only analysis":
            result["notes"].append("未获取完整仓库，无法确认所有间接调用方和完整回归范围。")
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
