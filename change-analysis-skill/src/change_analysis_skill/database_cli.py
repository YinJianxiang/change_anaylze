"""Command-line access to the optional MySQL read-only helper."""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

from .database import DatabaseError, query_read_only
from .skill import _load_env_file


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes user input (potentially SQL or credentials).
        raise DatabaseError("DB_INVALID_ARGUMENT")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description=(
        "Run one MySQL read-only query. Requires the database extra and a "
        "least-privilege read-only account; SQL screening cannot prevent every "
        "stored-function side effect. Timeout bounds connect/socket operations, "
        "not total query runtime."))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql")
    source.add_argument("--sql-file", type=Path)
    parser.add_argument("--params", default="[]", help="JSON array for %%s placeholders")
    parser.add_argument("--max-rows", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=10, help="Connect/read/write timeout in seconds")
    parser.add_argument("--output", type=Path, help="JSON output file; default stdout")
    return parser


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)  # Preserve exact precision, not a float approximation.
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "base64", "data": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError("unsupported database result type")


def main(argv: list[str] | None = None) -> int:
    stage = "DB_INVALID_ARGUMENT"
    try:
        args = build_parser().parse_args(argv)
        if args.output:
            destination = args.output.resolve()
            protected = {args.env_file.resolve()}
            if args.sql_file:
                protected.add(args.sql_file.resolve())
            if destination in protected or destination.name.lower() == ".env":
                raise DatabaseError("DB_INVALID_ARGUMENT")
        stage = "DB_CONFIG_ERROR"
        _load_env_file(args.env_file)
        stage = "DB_INPUT_ERROR"
        sql = args.sql if args.sql is not None else args.sql_file.read_text(encoding="utf-8-sig")
        try:
            params = json.loads(args.params, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError):
            raise DatabaseError("DB_INVALID_PARAMS") from None
        stage = "DB_QUERY_FAILED"
        result = query_read_only(sql, params, max_rows=args.max_rows, timeout=args.timeout)
        stage = "DB_OUTPUT_ERROR"
        rendered = json.dumps(result, ensure_ascii=False, indent=2, default=_json_value, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except DatabaseError as exc:
        # Only locally defined categories may reach the terminal, never driver
        # messages, SQL, arguments, configuration values or traceback text.
        known = {"DB_INVALID_ARGUMENT", "DB_CONFIG_ERROR", "DB_INPUT_ERROR", "DB_INVALID_PARAMS",
                 "DB_INVALID_SQL", "DB_READ_ONLY_VIOLATION", "DB_DRIVER_UNAVAILABLE",
                 "DB_CONNECTION_FAILED", "DB_QUERY_FAILED", "DB_UNEXPECTED_RESULT",
                 "DB_CLEANUP_FAILED", "DB_DUPLICATE_COLUMNS", "DB_OUTPUT_ERROR"}
        print(exc.code if exc.code in known else stage, file=sys.stderr)
        return 2
    except Exception:
        print(stage, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
