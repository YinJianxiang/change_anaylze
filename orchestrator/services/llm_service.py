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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from change_analysis_harness.coverage_reflector import (
    merge_reflection_into_analysis,
    reflect_analysis_result,
)
from change_analysis_harness.selection import resolve_effort


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
        compact: dict[str, Any] = {
            "message_type": str(result.get("message_type", "confirmation")),
            "summary": str(result.get("summary", ""))[:500],
        }
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

    @staticmethod
    def _diff_for_files(diff: str, files: list[str]) -> str:
        wanted = {path.replace("\\", "/") for path in files}
        starts = [match.start() for match in re.finditer(r"(?m)^diff --git ", diff)]
        if not starts:
            return diff if not wanted else ""
        parts: list[str] = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(diff)
            section = diff[start:end]
            match = re.match(r"diff --git a/(.*?) b/(.*?)\n", section)
            if not match:
                continue
            left = match.group(1).replace("\\", "/")
            right = match.group(2).replace("\\", "/")
            if left in wanted or right in wanted:
                parts.append(section)
        return "".join(parts)

    def _batches_from_impact_units(
        self,
        input_payload: dict[str, object],
        max_bytes: int,
    ) -> list[dict[str, object]] | None:
        units = input_payload.get("impact_units")
        repositories = input_payload.get("repositories")
        if not isinstance(units, list) or not units or not isinstance(repositories, list):
            return None
        batches: list[dict[str, object]] = []
        for unit in units:
            if not isinstance(unit, dict):
                continue
            repository_index = int(unit.get("repository_index") or 0)
            if repository_index < 0 or repository_index >= len(repositories):
                continue
            repository = repositories[repository_index]
            if not isinstance(repository, dict):
                continue
            files = [str(path) for path in (unit.get("files") or [])]
            diff = self._diff_for_files(str(repository.get("diff", "")), files)
            if not diff and files:
                # Fall back to whole-repo diff slice when headers cannot be matched.
                diff = str(repository.get("diff", ""))
            for chunk_files, chunk in self._split_diff(diff, max_bytes) or [([], "")]:
                batch_repository = dict(repository)
                batch_repository["diff"] = chunk or diff
                batch_repository["changed_files"] = chunk_files or files
                batch = dict(input_payload)
                batch["repositories"] = [batch_repository]
                batch["analysis_mode"] = "batch-change-analysis"
                batch["batch_repository_index"] = repository_index
                batch["impact_unit"] = unit
                batch["applicable_guide_sections"] = list(unit.get("applicable_guide_sections") or [])
                batches.append(batch)
        return batches or None

    def _batch_payloads(self, input_payload: dict[str, object], max_bytes: int) -> list[dict[str, object]]:
        unit_batches = self._batches_from_impact_units(input_payload, max_bytes)
        if unit_batches is not None:
            batches = unit_batches
        else:
            repositories = input_payload.get("repositories")
            if not isinstance(repositories, list):
                return [input_payload]
            batches = []
            for repository_index, repository in enumerate(repositories):
                if not isinstance(repository, dict):
                    continue
                diff = str(repository.get("diff", ""))
                for files, chunk in self._split_diff(diff, max_bytes):
                    batch_repository = dict(repository)
                    batch_repository["diff"] = chunk
                    batch_repository["changed_files"] = files
                    # Keep the evidence generated by the bundled Skill scripts in every
                    # batch so the Agent can follow callers and transitive impact instead
                    # of receiving only an isolated diff fragment.
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

    def _run_unit_batch(self, batch: dict[str, object]) -> dict[str, Any]:
        started = time.perf_counter()
        unit = batch.get("impact_unit") if isinstance(batch.get("impact_unit"), dict) else {}
        unit_id = str(unit.get("id") or f"batch-{batch.get('batch_index')}")
        try:
            response = self._analyze_with_retry(batch)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "batch_index": batch["batch_index"],
                "batch_count": batch["batch_count"],
                "repository_index": batch.get("batch_repository_index"),
                "changed_files": batch["repositories"][0]["changed_files"],
                "impact_unit_id": unit_id,
                "result": self._compact_result(response.get("result", response)),
                "metric": {
                    "unit_id": unit_id,
                    "elapsed_ms": elapsed_ms,
                    "status": "ok",
                    "skip_reason": None,
                },
            }
        except Exception as error:  # noqa: BLE001 - isolate unit failures
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "batch_index": batch["batch_index"],
                "batch_count": batch["batch_count"],
                "repository_index": batch.get("batch_repository_index"),
                "changed_files": batch["repositories"][0]["changed_files"],
                "impact_unit_id": unit_id,
                "result": {
                    "message_type": "confirmation",
                    "summary": f"ImpactUnit {unit_id} analysis failed",
                    "findings": [],
                    "risks": [],
                    "test_scope": [],
                    "uncertainties": [f"ImpactUnit {unit_id} failed: {error}"],
                },
                "metric": {
                    "unit_id": unit_id,
                    "elapsed_ms": elapsed_ms,
                    "status": "failed",
                    "skip_reason": str(error),
                },
            }

    def analyze(self, input_payload: dict[str, object]) -> dict[str, Any]:
        effort = resolve_effort(str(input_payload.get("effort") or "") or None)
        payload = dict(input_payload)
        payload["effort"] = effort
        max_bytes = max(int(os.environ.get("LLM_BATCH_DIFF_BYTES", "20000")), 1000)
        batches = self._batch_payloads(payload, max_bytes)
        if len(batches) == 1:
            started = time.perf_counter()
            unit = batches[0].get("impact_unit") if isinstance(batches[0].get("impact_unit"), dict) else {}
            unit_id = str(unit.get("id") or f"batch-{batches[0].get('batch_index', 1)}")
            response = self._analyze_with_retry(batches[0])
            return self._finalize_analysis(
                response,
                effort=effort,
                metrics={
                    "units": [{
                        "unit_id": unit_id,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "status": "ok",
                        "skip_reason": None,
                    }],
                    "concurrency": 1,
                },
            )

        concurrency = max(int(os.environ.get("LLM_UNIT_CONCURRENCY", "1")), 1)
        results: list[dict[str, Any]] = []
        metrics_units: list[dict[str, Any]] = []
        if concurrency == 1 or len(batches) == 1:
            for batch in batches:
                item = self._run_unit_batch(batch)
                metrics_units.append(item["metric"])
                results.append({key: value for key, value in item.items() if key != "metric"})
        else:
            ordered: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(self._run_unit_batch, batch): int(batch["batch_index"])
                    for batch in batches
                }
                for future in as_completed(futures):
                    item = future.result()
                    metrics_units.append(item["metric"])
                    ordered[int(item["batch_index"])] = {
                        key: value for key, value in item.items() if key != "metric"
                    }
            results = [ordered[index] for index in sorted(ordered)]

        repositories = []
        for repository in payload.get("repositories", []):
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
                    "mail_context": payload.get("mail_context") or {},
                    "requirements": payload.get("requirements") or [],
                    "requirement_urls": payload.get("requirement_urls", []),
                    "repositories": repositories,
                    "analysis_mode": "batch-summary",
                    "evidence_warnings": payload.get("evidence_warnings", []),
                    "batch_results": group,
                    "batch_count": len(group),
                    "summary_level": summary_level,
                    "summary_group_index": group_index,
                    "summary_group_count": len(groups),
                    "effort": effort,
                    "selection_plan": payload.get("selection_plan"),
                    "impact_units": payload.get("impact_units"),
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
                "mail_context": payload.get("mail_context") or {},
                "requirements": payload.get("requirements") or [],
                "requirement_urls": payload.get("requirement_urls", []),
                "repositories": repositories,
                "analysis_mode": "batch-summary",
                "batch_results": current_results,
                "evidence_warnings": payload.get("evidence_warnings", []),
                "effort": effort,
            })
        summary["batch_results"] = results
        summary["batch_count"] = len(results)
        summary["summary_levels"] = summary_level - 1
        return self._finalize_analysis(
            summary,
            effort=effort,
            metrics={"units": metrics_units, "concurrency": concurrency},
        )

    def _finalize_analysis(
        self,
        analysis: dict[str, Any],
        *,
        effort: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        reflection = reflect_analysis_result(analysis, effort=effort)
        finalized = merge_reflection_into_analysis(analysis, reflection)
        finalized["effort"] = effort
        finalized["analysis_metrics"] = metrics
        return finalized

    def continue_with_feedback(self, previous_result: dict[str, object], feedback: str) -> dict[str, Any]:
        """Continue the same analysis conversation with reviewer evidence/corrections."""
        raw = previous_result.get("raw")
        previous_response_id = raw.get("id", "") if isinstance(raw, dict) else ""
        payload = {
            "analysis_mode": "reviewer-feedback-final",
            "previous_analysis": previous_result,
            "reviewer_feedback": feedback,
            "instruction": (
                "Re-evaluate the previous skill result using the reviewer feedback. "
                "Return the final conclusion in the same schema; preserve supported findings, "
                "correct errors, and explicitly surface unresolved uncertainty."
            ),
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        response = self._analyze_with_retry(payload)
        result = response.get("result", response)
        if not isinstance(result, dict) or result.get("message_type") != "final":
            raise RuntimeError("Reviewer-feedback analysis must return message_type=final")
        effort = resolve_effort(str(previous_result.get("effort") or "") or None)
        return self._finalize_analysis(response, effort=effort, metrics={"units": []})
