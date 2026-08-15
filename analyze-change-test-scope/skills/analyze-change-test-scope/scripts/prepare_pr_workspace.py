#!/usr/bin/env python3
"""Prepare an isolated, read-only analysis workspace for a GitHub pull request."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9][0-9]*)/?(?:[?#].*)?$"
)
DEFAULT_TIMEOUT = 60


class PreparationError(RuntimeError):
    """Raised when a safe PR workspace cannot be prepared."""


@dataclass(frozen=True)
class PullRequestInfo:
    owner: str
    repo: str
    number: int
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    source: str


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = PR_URL_RE.fullmatch(url.strip())
    if not match:
        raise ValueError(
            "expected a GitHub pull request URL such as "
            "https://github.com/owner/repo/pull/123"
        )
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def run_command(
    args: list[str], *, cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT
) -> str:
    try:
        process = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreparationError(f"command timed out after {timeout}s: {args[0]}") from exc
    except OSError as exc:
        raise PreparationError(f"could not run {args[0]}: {exc}") from exc
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip() or "unknown error"
        raise PreparationError(f"{' '.join(args[:3])} failed: {detail}")
    return process.stdout


def _pr_info_from_gh(url: str, owner: str, repo: str, number: int, timeout: int) -> PullRequestInfo:
    if not shutil.which("gh"):
        raise PreparationError("gh is not installed")
    raw = run_command(
        [
            "gh",
            "pr",
            "view",
            url,
            "--json",
            "baseRefName,headRefName,baseRefOid,headRefOid",
        ],
        timeout=timeout,
    )
    data = json.loads(raw)
    return PullRequestInfo(
        owner=owner,
        repo=repo,
        number=number,
        base_branch=data["baseRefName"],
        head_branch=data["headRefName"],
        base_sha=data["baseRefOid"],
        head_sha=data["headRefOid"],
        source="gh",
    )


def _pr_info_from_api(owner: str, repo: str, number: int, timeout: int) -> PullRequestInfo:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "analyze-change-test-scope"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PreparationError(f"GitHub API fallback failed: {exc}") from exc
    return PullRequestInfo(
        owner=owner,
        repo=repo,
        number=number,
        base_branch=data["base"]["ref"],
        head_branch=data["head"]["ref"],
        base_sha=data["base"]["sha"],
        head_sha=data["head"]["sha"],
        source="github-api",
    )


def get_pr_info(url: str, timeout: int = DEFAULT_TIMEOUT) -> PullRequestInfo:
    owner, repo, number = parse_pr_url(url)
    errors: list[str] = []
    try:
        return _pr_info_from_gh(url, owner, repo, number, timeout)
    except (PreparationError, KeyError, json.JSONDecodeError) as exc:
        errors.append(f"gh: {exc}")
    try:
        return _pr_info_from_api(owner, repo, number, timeout)
    except (PreparationError, KeyError, TypeError) as exc:
        errors.append(f"GitHub API: {exc}")
    raise PreparationError(
        "could not obtain PR metadata. Install/authenticate gh or allow GitHub API access. "
        + " | ".join(errors)
    )


def _normalize_remote(value: str) -> str | None:
    value = value.strip().removesuffix(".git").removesuffix("/")
    patterns = (
        r"https?://github\.com/([^/]+/[^/]+)$",
        r"ssh://git@github\.com/([^/]+/[^/]+)$",
        r"git@github\.com:([^/]+/[^/]+)$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None


def repository_root(path: Path, timeout: int = DEFAULT_TIMEOUT) -> Path | None:
    try:
        output = run_command(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"], timeout=timeout
        )
    except PreparationError:
        return None
    return Path(output.strip()).resolve()


def matching_remote(repo: Path, expected: str, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    remotes = run_command(["git", "-C", str(repo), "remote"], timeout=timeout).splitlines()
    for remote in remotes:
        url = run_command(
            ["git", "-C", str(repo), "remote", "get-url", remote], timeout=timeout
        ).strip()
        if _normalize_remote(url) == expected.lower():
            return remote
    return None


def _fetch_pr_refs(repo: Path, remote: str, info: PullRequestInfo, timeout: int) -> None:
    namespace = f"refs/analyze-change-test-scope/pr-{info.number}"
    run_command(
        [
            "git",
            "-C",
            str(repo),
            "fetch",
            "--no-tags",
            remote,
            f"+refs/heads/{info.base_branch}:{namespace}/base",
            f"+refs/pull/{info.number}/head:{namespace}/head",
        ],
        timeout=timeout,
    )


def _add_worktree(repo: Path, workspace: Path, head_sha: str, timeout: int) -> None:
    if workspace.exists():
        existing = repository_root(workspace, timeout)
        if existing:
            current = run_command(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"], timeout=timeout
            ).strip()
            if current == head_sha:
                return
        raise PreparationError(f"workspace already exists with different content: {workspace}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(workspace), head_sha],
        timeout=timeout,
    )


def prepare_workspace(
    pr_url: str,
    *,
    repo_path: Path | None = None,
    cache_dir: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    info_provider: Callable[[str, int], PullRequestInfo] = get_pr_info,
) -> dict[str, Any]:
    info = info_provider(pr_url, timeout)
    expected = f"{info.owner}/{info.repo}"
    cache = (cache_dir or Path(tempfile.gettempdir()) / "analyze-change-test-scope").resolve()

    candidate = repository_root((repo_path or Path.cwd()).expanduser().resolve(), timeout)
    remote = matching_remote(candidate, expected, timeout) if candidate else None
    if candidate and remote:
        source_repo = candidate
        repository_source = "existing-local-repository"
    else:
        source_repo = cache / "repositories" / f"{info.owner}-{info.repo}"
        repository_source = "cached-clone"
        if source_repo.exists():
            root = repository_root(source_repo, timeout)
            remote = matching_remote(root, expected, timeout) if root else None
            if not root or not remote:
                raise PreparationError(f"cache path is not the expected repository: {source_repo}")
            source_repo = root
        else:
            source_repo.parent.mkdir(parents=True, exist_ok=True)
            run_command(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    f"https://github.com/{expected}.git",
                    str(source_repo),
                ],
                timeout=timeout,
            )
            remote = "origin"

    if remote is None:
        raise PreparationError("could not resolve a safe remote for the target repository")
    active_branch = run_command(
        ["git", "-C", str(source_repo), "branch", "--show-current"], timeout=timeout
    ).strip()
    _fetch_pr_refs(source_repo, remote, info, timeout)
    merge_base = run_command(
        ["git", "-C", str(source_repo), "merge-base", info.base_sha, info.head_sha],
        timeout=timeout,
    ).strip()
    workspace = cache / "worktrees" / f"{info.owner}-{info.repo}-pr-{info.number}-{info.head_sha[:12]}"
    _add_worktree(source_repo, workspace, info.head_sha, timeout)
    branch_after = run_command(
        ["git", "-C", str(source_repo), "branch", "--show-current"], timeout=timeout
    ).strip()
    if branch_after != active_branch:
        raise PreparationError("safety check failed: the source repository branch changed")

    return {
        "repository": expected,
        "pr_number": info.number,
        "workspace": str(workspace),
        "repository_source": repository_source,
        "pr_info_source": info.source,
        "base_branch": info.base_branch,
        "head_branch": info.head_branch,
        "base_sha": info.base_sha,
        "head_sha": info.head_sha,
        "merge_base": merge_base,
        "range": f"{merge_base}...{info.head_sha}",
        "analysis_mode": "Full repository context analysis",
        "source_repository_branch_unchanged": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-url", required=True, help="GitHub pull request URL")
    parser.add_argument("--repo", help="Existing repository candidate (default: current directory)")
    parser.add_argument("--cache-dir", help="Cache/worktree parent (default: system temporary directory)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_workspace(
            args.pr_url,
            repo_path=Path(args.repo) if args.repo else None,
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            timeout=max(args.timeout, 1),
        )
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (ValueError, PreparationError, OSError, json.JSONDecodeError) as exc:
        json.dump(
            {
                "error": str(exc),
                "analysis_mode": "Diff-only analysis",
                "analysis_limit": "未获取完整仓库，无法确认所有间接调用方和完整回归范围。",
            },
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
