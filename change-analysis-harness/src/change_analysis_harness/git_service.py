"""Git analysis service boundary."""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Any


class GitService:
    """Fetch repositories and produce a pinned change snapshot."""

    def snapshot_repository(
        self,
        *,
        repository: Path,
        branch: str,
        base_branch: str = "master",
        repository_name: str = "repository",
        fetch_remote: bool = True,
    ) -> dict[str, Any]:
        """Collect a snapshot from a checkout without project configuration.

        This direct path keeps the input surface to a local
        checkout plus two branch names.
        """
        local_path = Path(repository)
        if not local_path.exists():
            raise ValueError(f"Repository path does not exist: {local_path}")

        git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        auth_header = os.environ.get("GIT_AUTH_HEADER", "").strip()
        token = os.environ.get("GIT_PERSONAL_ACCESS_TOKEN", "").strip()
        if token and not auth_header:
            username = os.environ.get("GIT_USERNAME", "oauth2").strip()
            credentials = base64.b64encode(f"{username}:{token}".encode()).decode()
            auth_header = f"Authorization: Basic {credentials}"

        def run(*args: str) -> str:
            command = ["git"]
            if auth_header:
                command.extend(["-c", f"http.extraHeader={auth_header}"])
            command.extend(args)
            completed = subprocess.run(
                command,
                cwd=local_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=git_env,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
            return (completed.stdout or "").strip()

        if fetch_remote:
            run("fetch", "origin", base_branch, branch)
        base_ref = f"origin/{base_branch}"
        target_ref = f"origin/{branch}"
        base_commit = run("rev-parse", base_ref)
        target_commit = run("rev-parse", target_ref)
        current_merge_base = run("merge-base", base_ref, target_ref)
        merge_commit = ""
        merge_base = current_merge_base
        if current_merge_base == target_commit:
            for commit in run("rev-list", "--first-parent", "--merges", base_ref).splitlines():
                parents = run("show", "-s", "--format=%P", commit).split()
                if target_commit in parents[1:]:
                    merge_commit = commit
                    merge_base = run("merge-base", parents[0], target_commit)
                    break

        diff = run("diff", "--no-ext-diff", merge_base, target_commit)
        files = run("diff", "--name-only", merge_base, target_commit).splitlines()
        commit_lines = run(
            "log",
            "--reverse",
            "--format=%H%x09%an%x09%aI%x09%s",
            f"{merge_base}..{target_commit}",
        ).splitlines()
        commits = []
        for line in commit_lines:
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append(
                    {"commit": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
                )
        return {
            "project": repository_name,
            "repository": str(local_path),
            "local_path": str(local_path),
            "base_branch": base_branch,
            "target_branch": branch,
            "base_commit": base_commit,
            "merge_base": merge_base,
            "target_commit": target_commit,
            "merge_commit": merge_commit or None,
            "commits": commits,
            "changed_files": files,
            "diff": diff,
        }
