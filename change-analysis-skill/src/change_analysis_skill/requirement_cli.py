"""Fetch TAPD or DingTalk requirement documents for Codex."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .skill import SkillInputError, _load_env_file
from .requirement_service import RequirementService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a TAPD story or DingTalk document for direct Codex analysis."
    )
    parser.add_argument("requirement_url", help="TAPD story URL or DingTalk document URL")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file containing TAPD/DingTalk credentials",
    )
    parser.add_argument(
        "--output", type=Path,
        help="Write the requirement evidence JSON to this file instead of stdout",
    )
    return parser


def run(requirement_url: str) -> dict[str, Any]:
    url = requirement_url.strip()
    if not url:
        raise SkillInputError("requirement_url must not be empty")
    documents = RequirementService().prepare([url]).get("documents", [])
    usable = [document for document in documents
              if document.get("status") in {"FETCHED", "PARTIAL"}
              and isinstance(document.get("content"), str) and document["content"].strip()]
    if not usable:
        status = "UNSUPPORTED" if documents and all(
            document.get("status") == "UNSUPPORTED" for document in documents
        ) else "FAILED"
    elif len(usable) != len(documents) or any(
        document.get("status") != "FETCHED"
        or document.get("meta", {}).get("truncated")
        or any(nested.get("status") != "FETCHED"
               for nested in document.get("meta", {}).get("nested", []))
        for document in usable
    ):
        status = "PARTIAL"
    else:
        status = "READY_FOR_CODEX"
    return {
        "status": status,
        "operation": "requirement_document_fetch",
        "requirement_documents": documents,
        "analysis": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _load_env_file(args.env_file)
        result = run(args.requirement_url)
    except (SkillInputError, OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"requirement-document: {error}\n")

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(rendered)
    return {"READY_FOR_CODEX": 0, "PARTIAL": 1}.get(result["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
