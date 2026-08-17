"""Streamable HTTP MCP server for the DingTalk enterprise agent."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from orchestrator.mail_ingest import DEFAULT_ENV_FILE, load_env_file
from orchestrator.services.change_analysis_tools import ChangeAnalysisTools
from orchestrator.project_config import load_projects_config


def create_mcp(projects: dict[str, dict[str, str]], task_database: Path | None = None) -> FastMCP:
    tools = ChangeAnalysisTools(projects, task_database=task_database)
    server = FastMCP("change-analysis", stateless_http=True, json_response=True)
    server.tool()(tools.prepare_change_workspace)
    server.tool()(tools.get_mail_analysis_task)
    server.tool()(tools.collect_change_context)
    server.tool()(tools.collect_repository_impact)
    server.tool()(tools.read_source_evidence)
    server.tool()(tools.search_repository)
    server.tool()(tools.release_change_workspace)
    server.tool()(tools.complete_mail_analysis_task)
    return server


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path.rstrip("/").endswith("/mcp"):
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {self.token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the change-analysis MCP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--config", type=Path, default=Path(".local/projects.json"))
    parser.add_argument("--database", type=Path, default=Path(".local/mail_ingest.sqlite3"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()
    load_env_file(args.env_file)
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not token:
        raise SystemExit("MCP_AUTH_TOKEN is required")
    mcp = create_mcp(load_projects_config(args.config), args.database)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token=token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
