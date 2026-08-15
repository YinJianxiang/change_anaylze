"""Standalone HTTP server for change-analysis LLM requests."""
from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from orchestrator.mail_ingest import DEFAULT_ENV_FILE, load_env_file
from orchestrator.task_runner import PermanentTaskError, analyze_with_openai

LOGGER = logging.getLogger(__name__)


def validate_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    aliases = {"requirements": ("requirements", "requirement_urls", "requirement_documents"),
               "repositories": ("repositories", "repository", "repository_metadata")}
    missing = [key for key, names in aliases.items() if not any(name in value for name in names)]
    if missing:
        raise ValueError("missing required fields: " + ", ".join(missing))
    if "requirements" not in value:
        value["requirements"] = value.get("requirement_documents", value.get("requirement_urls"))
    if "repositories" not in value:
        repository = value.get("repository", value.get("repository_metadata"))
        if isinstance(repository, dict) and any(key in value for key in ("commits", "changed_files", "diff")):
            repository = {**repository, **{key: value.get(key) for key in ("commits", "changed_files", "diff")}}
        value["repositories"] = [repository] if isinstance(repository, dict) else repository
    if not isinstance(value["requirements"], (list, dict)):
        raise ValueError("requirements must be an array or object")
    if not isinstance(value["repositories"], list) or not value["repositories"]:
        raise ValueError("repositories must be a non-empty array")
    for repository in value["repositories"]:
        if not isinstance(repository, dict):
            raise ValueError("each repository must be an object")
        for key in ("commits", "changed_files"):
            if not isinstance(repository.get(key), list):
                raise ValueError(f"repository.{key} must be an array")
        if not isinstance(repository.get("diff"), str):
            raise ValueError("repository.diff must be a string")
        for key in ("change_context", "repository_impact"):
            if key in repository and repository[key] is not None and not isinstance(repository[key], dict):
                raise ValueError(f"repository.{key} must be an object or null")
    if "analysis_mode" in value and not isinstance(value["analysis_mode"], str):
        raise ValueError("analysis_mode must be a string")
    if "evidence_warnings" in value and not isinstance(value["evidence_warnings"], list):
        raise ValueError("evidence_warnings must be an array")
    return value


def create_handler(analyzer: Callable[[dict[str, object]], dict[str, Any]] = analyze_with_openai):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send(200, {"status": "ok"}) if self.path == "/healthz" else self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/analyze":
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = validate_request(json.loads(self.rfile.read(length)))
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})
                return
            try:
                self._send(200, analyzer(payload))
            except PermanentTaskError as exc:
                self._send(422, {"error": str(exc)})
            except Exception:
                LOGGER.exception("LLM analysis failed")
                self._send(502, {"error": "LLM provider request failed"})

        def log_message(self, fmt: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def serve(host: str = "0.0.0.0", port: int = 8081) -> None:
    server = ThreadingHTTPServer((host, port), create_handler())
    LOGGER.info("llm server listening on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standalone LLM analysis server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()
    load_env_file(args.env_file)
    logging.basicConfig(level=logging.INFO)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
