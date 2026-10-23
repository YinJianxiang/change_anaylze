"""Optional, conservative MySQL read-only query helper.

SQL screening is defense in depth, NOT a replacement for a least-privilege
read-only database account. Even SELECT may invoke stored functions with side
effects. Only use trusted schemas/functions. Timeouts bound driver connect and
socket read/write operations, not the total wall-clock lifetime of a query.
"""
from __future__ import annotations

import importlib
import math
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit


class DatabaseError(Exception):
    """A fixed safe category; never contains driver messages, SQL or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_STARTERS = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"}
_FORBIDDEN = set("""INSERT UPDATE DELETE REPLACE MERGE CREATE ALTER DROP TRUNCATE
RENAME GRANT REVOKE CALL DO HANDLER LOAD OUTFILE DUMPFILE INTO SET USE PREPARE
EXECUTE DEALLOCATE LOCK UNLOCK SHARE START BEGIN COMMIT ROLLBACK SAVEPOINT RELEASE XA
ANALYZE OPTIMIZE REPAIR INSTALL UNINSTALL SHUTDOWN KILL FLUSH RESET PURGE
SLEEP BENCHMARK GET_LOCK RELEASE_LOCK RELEASE_ALL_LOCKS IS_FREE_LOCK IS_USED_LOCK
LOAD_FILE MASTER_POS_WAIT SOURCE_POS_WAIT WAIT_FOR_EXECUTED_GTID_SET
WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS SERVICE_GET_READ_LOCKS SERVICE_GET_WRITE_LOCKS
SERVICE_RELEASE_LOCKS""".split())
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")


def _scan(sql: str) -> tuple[str, list[tuple[str, int]]]:
    """Mask literals/comments but retain quoted names for function checks.

    Backslashes inside quotes are rejected: their interpretation changes with
    NO_BACKSLASH_ESCAPES. Double quotes may be strings or ANSI_QUOTES identifiers.
    """
    out = list(sql)
    quoted: list[tuple[str, int]] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch in "'\"`":
            start, quote = i, ch
            i += 1
            value = ""
            while i < len(sql):
                if sql[i] == "\\":
                    raise DatabaseError("DB_INVALID_SQL")
                if sql[i] == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        value += quote
                        i += 2
                        continue
                    i += 1
                    break
                value += sql[i]
                i += 1
            else:
                raise DatabaseError("DB_INVALID_SQL")
            quoted.append((value.upper(), i))
            out[start:i] = " " * (i - start)
        elif sql.startswith("/*", i):
            if sql.startswith(("/*!", "/*M!", "/*m!"), i):
                raise DatabaseError("DB_READ_ONLY_VIOLATION")
            end = sql.find("*/", i + 2)
            if end < 0:
                raise DatabaseError("DB_INVALID_SQL")
            end += 2
            out[i:end] = " " * (end - i)
            i = end
        elif ch == "#" or (sql.startswith("--", i) and
                                  (i + 2 == len(sql) or ord(sql[i + 2]) <= 32)):
            end = i
            while end < len(sql) and sql[end] not in "\r\n":
                end += 1
            out[i:end] = " " * (end - i)
            i = end
        else:
            i += 1
    return "".join(out), quoted


def validate_read_only_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip() or "\x00" in sql:
        raise DatabaseError("DB_INVALID_SQL")
    masked, quoted = _scan(sql)
    stripped = masked.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped or "@" in masked or ":=" in masked:
        raise DatabaseError("DB_READ_ONLY_VIOLATION")
    words = [match.group().upper() for match in _WORD.finditer(stripped)]
    # Require an actual unquoted statement starter, not a token inside parentheses.
    first = _WORD.match(stripped)
    if not first or first.group().upper() not in _STARTERS:
        raise DatabaseError("DB_READ_ONLY_VIOLATION")
    if any(word in _FORBIDDEN for word in words):
        raise DatabaseError("DB_READ_ONLY_VIOLATION")
    if words[0] == "WITH" and "SELECT" not in words:
        raise DatabaseError("DB_READ_ONLY_VIOLATION")
    for value, end in quoted:
        # Comments and whitespace can separate a quoted function name and '('.
        if value in _FORBIDDEN and masked[end:].lstrip().startswith("("):
            raise DatabaseError("DB_READ_ONLY_VIOLATION")
    return sql.strip()


def _limits(max_rows: int, timeout: float) -> None:
    if type(max_rows) is not int or not 1 <= max_rows <= 100000:
        raise DatabaseError("DB_INVALID_ARGUMENT")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 31536000 or not math.isfinite(timeout):
        raise DatabaseError("DB_INVALID_ARGUMENT")


def _params(sql: str, params: Any) -> list[Any]:
    if params is None:
        params = []
    if not isinstance(params, list):
        raise DatabaseError("DB_INVALID_PARAMS")
    for value in params:
        if not (value is None or type(value) in (str, int, float, bool, bytes)):
            raise DatabaseError("DB_INVALID_PARAMS")
        if isinstance(value, float) and not math.isfinite(value):
            raise DatabaseError("DB_INVALID_PARAMS")
    masked, _ = _scan(sql)
    placeholders = len(re.findall(r"(?<!%)%s", masked.replace("%%", "  ")))
    if placeholders != len(params):
        raise DatabaseError("DB_INVALID_PARAMS")
    # PyMySQL uses percent-formatting on the entire statement when args exist.
    # Reject literal/comment placeholders and unsupported format codes.
    if params:
        i = 0
        while i < len(sql):
            if sql[i] != "%":
                i += 1
                continue
            pair = sql[i:i + 2]
            if pair == "%%":
                i += 2
            elif pair == "%s" and masked[i:i + 2] == "%s":
                i += 2
            else:
                raise DatabaseError("DB_INVALID_PARAMS")
    return params


def connection_options(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Parse explicit supported connection inputs; never forward URL options."""
    env = os.environ if environ is None else environ
    try:
        if env.get("DATABASE_URL"):
            url = urlsplit(env["DATABASE_URL"])
            if url.scheme not in {"mysql", "mysql+pymysql"} or url.query or url.fragment:
                raise ValueError
            options = {"host": url.hostname, "port": 3306 if url.port is None else url.port,
                       "user": unquote(url.username or ""),
                       "password": unquote(url.password or ""),
                       "database": unquote(url.path.removeprefix("/"))}
        else:
            options = {"host": env.get("DB_HOST"), "port": int(env.get("DB_PORT", "3306")),
                       "user": env.get("DB_USER"), "password": env.get("DB_PASSWORD", ""),
                       "database": env.get("DB_NAME")}
        if not all(options[key] for key in ("host", "user", "database")) or not 1 <= options["port"] <= 65535:
            raise ValueError
        options["charset"] = "utf8mb4"
        return options
    except Exception:
        raise DatabaseError("DB_CONFIG_ERROR") from None


def query_read_only(sql: str, params: list[Any] | None = None, *,
                    environ: Mapping[str, str] | None = None,
                    max_rows: int = 100, timeout: float = 10) -> dict[str, Any]:
    """Execute one screened statement, fetching at most max_rows + 1 rows.

    Uses an unbuffered cursor to avoid buffering the whole result. An unfinished
    result is discarded by closing its socket before cursor cleanup. No fallback
    to a writable transaction is permitted. All errors have safe fixed messages.
    """
    sql = validate_read_only_sql(sql)
    _limits(max_rows, timeout)
    params = _params(sql, params)
    options = connection_options(environ)
    try:
        pymysql = importlib.import_module("pymysql")
    except ImportError:
        raise DatabaseError("DB_DRIVER_UNAVAILABLE") from None
    options.update(connect_timeout=timeout, read_timeout=timeout, write_timeout=timeout,
                   autocommit=False, local_infile=False,
                   cursorclass=pymysql.cursors.SSCursor)
    connection = None
    cursor = None
    truncated = False
    error = None
    result = None
    try:
        connection = pymysql.connect(**options)
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute(sql, tuple(params) if params else None)
        if cursor.description is None:
            raise DatabaseError("DB_UNEXPECTED_RESULT")
        fields = [column[0] for column in cursor.description]
        if len(set(fields)) != len(fields):
            raise DatabaseError("DB_DUPLICATE_COLUMNS")
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [dict(zip(fields, row)) for row in fetched[:max_rows]]
        result = {"rows": rows, "fields": fields, "row_count": len(rows), "truncated": truncated}
    except DatabaseError as exc:
        error = exc.code
    except Exception:
        error = "DB_QUERY_FAILED" if connection is not None else "DB_CONNECTION_FAILED"
    finally:
        if connection is not None:
            # With SSCursor, rollback/cursor.close may drain a huge remaining
            # result. Closing the transport first aborts it and rolls back at
            # the server; still attempt rollback and close in every case.
            if truncated or error:
                try:
                    connection.close()
                except Exception:
                    error = error or "DB_CLEANUP_FAILED"
            try:
                connection.rollback()
            except Exception:
                if not truncated and not error:
                    error = "DB_CLEANUP_FAILED"
            try:
                connection.close()
            except Exception:
                error = error or "DB_CLEANUP_FAILED"
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass  # Socket already closed; never drain remaining rows.
    if error:
        raise DatabaseError(error) from None
    return result
