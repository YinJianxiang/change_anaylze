"""Read-only Git snapshot service."""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Any


class GitService:
    def snapshot(self, project: str, branch: str,
                 config: dict[str, dict[str, str]]) -> dict[str, Any]:
        settings = config.get(project)
        if not settings:
            raise ValueError(f"Missing repository mapping for project: {project}")
        local_path = Path(settings.get("local_path", ""))
        if not local_path.exists():
            raise ValueError(f"Repository path does not exist for {project}: {local_path}")
        base_branch = settings.get("base_branch", "master")
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
                command, cwd=local_path, capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False, env=git_env,
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr.strip() or "git command failed")
            return (completed.stdout or "").strip()

        run("fetch", "origin", base_branch, branch)
        base_ref, target_ref = f"origin/{base_branch}", f"origin/{branch}"
        base_commit = run("rev-parse", base_ref)
        target_commit = run("rev-parse", target_ref)
        merge_base = run("merge-base", base_ref, target_ref)
        diff = run("diff", "--no-ext-diff", merge_base, target_commit)
        files = run("diff", "--name-only", merge_base, target_commit).splitlines()
        commit_lines = run(
            "log", "--reverse", "--format=%H%x09%an%x09%aI%x09%s",
            f"{merge_base}..{target_commit}",
        ).splitlines()
        commits = []
        for line in commit_lines:
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append(dict(zip(("commit", "author", "date", "subject"), parts)))
        return {
            "project": project, "repository": settings.get("repository", ""),
            "local_path": str(local_path), "base_branch": base_branch,
            "target_branch": branch, "base_commit": base_commit,
            "merge_base": merge_base, "target_commit": target_commit,
            "commits": commits, "changed_files": files, "diff": diff,
        }
