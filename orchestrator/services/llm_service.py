"""LLM analysis service boundary."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class LLMService:
    def __init__(self, url: str | None = None) -> None:
        configured = url or os.environ.get("LLM_SERVER_URL", "")
        self.url = configured.rstrip("/") if configured else None

    def _analyze_once(self, input_payload: dict[str, object]) -> dict[str, Any]:
        if self.url is None:
            from orchestrator.task_runner import analyze_with_openai
            return analyze_with_openai(input_payload)
        data = json.dumps(input_payload, ensure_ascii=False).encode("utf-8")
        debug_dir = os.environ.get("LLM_DEBUG_DUMP_DIR", "").strip()
        if debug_dir:
            directory = Path(debug_dir)
            directory.mkdir(parents=True, exist_ok=True)
            debug_file = directory / f"llm-http-request-{uuid.uuid4().hex}.json"
            debug_file.write_text(json.dumps({
                "url": self.url,
                "payload_bytes": len(data),
                "request": input_payload,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"LLM debug request written to {debug_file}", file=sys.stderr)
        request = urllib.request.Request(self.url, data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            value = json.load(response)
        if not isinstance(value, dict):
            raise RuntimeError("LLM server returned a non-object response")
        return value

    def _analyze_with_retry(self, input_payload: dict[str, object]) -> dict[str, Any]:
        attempts = max(int(os.environ.get("LLM_BATCH_MAX_ATTEMPTS", "3")), 1)
        for attempt in range(1, attempts + 1):
            try:
                return self._analyze_once(input_payload)
            except urllib.error.HTTPError as error:
                if error.code not in {502, 504} or attempt == attempts:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == attempts:
                    raise
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("LLM request retry loop exited unexpectedly")

    @staticmethod
    def _compact_result(value: object) -> dict[str, Any]:
        result = value if isinstance(value, dict) else {}
        limits = {"findings": (5, 300), "risks": (5, 300), "test_scope": (8, 400), "uncertainties": (5, 300)}
        compact: dict[str, Any] = {"summary": str(result.get("summary", ""))[:500]}
        for key, (count, length) in limits.items():
            items = result.get(key, [])
            compact[key] = [str(item)[:length] for item in items[:count]] if isinstance(items, list) else []
        return compact

    @staticmethod
    def _split_diff(diff: str, max_bytes: int) -> list[tuple[list[str], str]]:
        starts = [match.start() for match in re.finditer(r"(?m)^diff --git ", diff)]
        sections = [diff[starts[index]:starts[index + 1] if index + 1 < len(starts) else len(diff)]
                    for index in range(len(starts))] if starts else [diff]
        chunks: list[tuple[list[str], str]] = []
        current_sections: list[str] = []
        current_files: list[str] = []
        current_size = 0

        def emit() -> None:
            nonlocal current_sections, current_files, current_size
            if current_sections:
                chunks.append((current_files, "".join(current_sections)))
            current_sections, current_files, current_size = [], [], 0

        for section in sections:
            match = re.match(r"diff --git a/(.*?) b/", section)
            filename = match.group(1) if match else "unknown"
            encoded = section.encode("utf-8")
            if len(encoded) > max_bytes:
                emit()
                for offset in range(0, len(encoded), max_bytes):
                    part = encoded[offset:offset + max_bytes].decode("utf-8", errors="ignore")
                    chunks.append(([filename], part + "\n[continued diff section]\n"))
                continue
            if current_size + len(encoded) > max_bytes:
                emit()
            current_sections.append(section)
            current_files.append(filename)
            current_size += len(encoded)
        emit()
        return chunks

    def _batch_payloads(self, input_payload: dict[str, object], max_bytes: int) -> list[dict[str, object]]:
        repositories = input_payload.get("repositories")
        if not isinstance(repositories, list):
            return [input_payload]
        batches: list[dict[str, object]] = []
        for repository_index, repository in enumerate(repositories):
            if not isinstance(repository, dict):
                continue
            diff = str(repository.get("diff", ""))
            for files, chunk in self._split_diff(diff, max_bytes):
                batch_repository = dict(repository)
                batch_repository["diff"] = chunk
                batch_repository["changed_files"] = files
                batch_repository["change_context"] = None
                batch_repository["repository_impact"] = None
                batch = dict(input_payload)
                batch["repositories"] = [batch_repository]
                batch["analysis_mode"] = "batch-change-analysis"
                batch["batch_repository_index"] = repository_index
                batches.append(batch)
        total = len(batches)
        for index, batch in enumerate(batches, start=1):
            batch["batch_index"] = index
            batch["batch_count"] = total
        return batches or [input_payload]

    def analyze(self, input_payload: dict[str, object]) -> dict[str, Any]:
        max_bytes = max(int(os.environ.get("LLM_BATCH_DIFF_BYTES", "20000")), 1000)
        batches = self._batch_payloads(input_payload, max_bytes)
        if len(batches) == 1:
            return self._analyze_with_retry(batches[0])
        results = []
        for batch in batches:
            response = self._analyze_with_retry(batch)
            results.append({
                "batch_index": batch["batch_index"],
                "batch_count": batch["batch_count"],
                "repository_index": batch["batch_repository_index"],
                "changed_files": batch["repositories"][0]["changed_files"],
                "result": self._compact_result(response.get("result", response)),
            })
        repositories = []
        for repository in input_payload.get("repositories", []):
            metadata = dict(repository)
            metadata.update({"diff": "", "commits": metadata.get("commits", []), "changed_files": []})
            metadata.pop("change_context", None)
            metadata.pop("repository_impact", None)
            repositories.append(metadata)
        group_size = max(int(os.environ.get("LLM_SUMMARY_GROUP_SIZE", "2")), 2)
        current_results = results
        summary: dict[str, Any] | None = None
        summary_level = 1
        while len(current_results) > 1:
            next_results = []
            groups = [current_results[index:index + group_size]
                      for index in range(0, len(current_results), group_size)]
            for group_index, group in enumerate(groups, start=1):
                summary_payload = {
                    "requirements": input_payload.get("requirements") or [],
                    "requirement_urls": input_payload.get("requirement_urls", []),
                    "repositories": repositories,
                    "analysis_mode": "batch-summary",
                    "batch_results": group,
                    "batch_count": len(group),
                    "summary_level": summary_level,
                    "summary_group_index": group_index,
                    "summary_group_count": len(groups),
                }
                summary = self._analyze_with_retry(summary_payload)
                next_results.append({
                    "summary_level": summary_level,
                    "summary_group_index": group_index,
                    "result": self._compact_result(summary.get("result", summary)),
                })
            current_results = next_results
            summary_level += 1
        if summary is None:
            summary = self._analyze_with_retry({
                "requirements": input_payload.get("requirements") or [],
                "repositories": repositories,
                "analysis_mode": "batch-summary",
                "batch_results": current_results,
            })
        summary["batch_results"] = results
        summary["batch_count"] = len(results)
        summary["summary_levels"] = summary_level - 1
        return summary
