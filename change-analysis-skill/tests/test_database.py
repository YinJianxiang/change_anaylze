"""Pure mock tests: never import a real driver or contact a database."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from change_analysis_skill import database as db, database_cli as cli

ENV = {"DB_HOST": "db.invalid", "DB_USER": "reader", "DB_PASSWORD": "secret", "DB_NAME": "app"}


class SqlTests(unittest.TestCase):
    def test_read_queries_and_masking(self):
        queries = ["SELECT 1", "SELECT 1; -- end", "# comment\n SHOW TABLES", "DESC users",
                   "DESCRIBE users", "EXPLAIN SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x",
                   "SELECT 'DELETE; /*! SLEEP(1) */', 'a''b'", 'SELECT "DROP;"',
                   "SELECT `update` FROM users", "SELECT 1 /* DELETE; */", "SELECT 1--2",
                   "SELECT 1 -- DELETE\n", "SELECT 'a#b'", "SELECT '/*M!foo*/'"]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assertEqual(db.validate_read_only_sql(sql), sql.strip())

    def test_denies_unsafe_statements_and_obfuscation(self):
        queries = ["UPDATE users SET a=1", "SELECT 1; SELECT 2", "SELECT 1;;", "SET @x=1",
                   "WITH x AS (SELECT 1) DELETE FROM users", "SELECT * FROM users FOR UPDATE",
                   "SELECT * FROM users FOR SHARE", "SELECT * FROM users LOCK IN SHARE MODE",
                   "SELECT 1 INTO OUTFILE '/tmp/foo'", "SELECT 1 INTO DUMPFILE '/tmp/foo'",
                   "SELECT SLEEP(1)", "SELECT BENCHMARK(1,1)", "SELECT GET_LOCK('x',1)",
                   "SELECT RELEASE_LOCK('x')", "SELECT LOAD_FILE('/etc/passwd')",
                   "SELECT @x := 1", "SELECT @x", "SELECT 1 /*!50000 INTO OUTFILE 'x' */",
                   "SELECT 1 /*M!100100 x */", "SELECT 1 /*m!100100 x */",
                   "SELECT `SLEEP`(1)", 'SELECT "sleep" /* gap */ (1)',
                   "SELECT db.`GET_LOCK`('x',1)", "SELECT `LOAD_FILE`/**/('x')",
                   "EXPLAIN DELETE FROM users", "WITH x AS (SHOW TABLES) SHOW TABLES",
                   "CALL danger()", "SELECT WAIT_FOR_EXECUTED_GTID_SET('x')",
                   "SELECT SERVICE_GET_WRITE_LOCKS('x',1)"]
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(db.DatabaseError):
                db.validate_read_only_sql(sql)

    def test_invalid_lexical_inputs(self):
        for sql in [None, 1, "", "  ", "SELECT 'x", 'SELECT "x', "SELECT `x", "SELECT /* x",
                    "SELECT '\\'; DELETE FROM x; -- '", "SELECT 1\x00"]:
            with self.subTest(sql=sql), self.assertRaises(db.DatabaseError):
                db.validate_read_only_sql(sql)

    def test_params(self):
        self.assertEqual(db._params("SELECT %s, %s, %s", [1, "' OR 1=1", None]), [1, "' OR 1=1", None])
        self.assertEqual(db._params("SELECT '100%%', %s", [1]), [1])
        self.assertEqual(db._params("SELECT 5 % 2", []), [])
        for sql, params in [("SELECT %s", []), ("SELECT 1", [1]), ("SELECT %s", (1,)),
                            ("SELECT %s", {"x": 1}), ("SELECT %s", [[1]]),
                            ("SELECT %s", [{"x": 1}]), ("SELECT %s", [float("nan")]),
                            ("SELECT %s", [float("inf")]), ("SELECT %s, '%s'", [1]),
                            ("SELECT %s -- %s", [1]), ("SELECT %d", [1]),
                            ("SELECT %(x)s", [1]), ("SELECT 10 % 2, %s", [1])]:
            with self.subTest(sql=sql, params=params), self.assertRaises(db.DatabaseError):
                db._params(sql, params)


class ConfigurationTests(unittest.TestCase):
    def test_separate_environment(self):
        options = db.connection_options(ENV)
        self.assertEqual(options, {"host": "db.invalid", "port": 3306, "user": "reader",
                                   "password": "secret", "database": "app", "charset": "utf8mb4"})
        self.assertEqual(db.connection_options({**ENV, "DB_PORT": "3307"})["port"], 3307)
        env = {key: val for key, val in ENV.items() if key != "DB_PASSWORD"}
        self.assertEqual(db.connection_options(env)["password"], "")

    def test_urls_and_precedence(self):
        for scheme in ["mysql", "mysql+pymysql"]:
            options = db.connection_options({**ENV, "DATABASE_URL": f"{scheme}://a%40b:p%3Ass@localhost:3307/my%20db"})
            self.assertEqual(options["user"], "a@b")
            self.assertEqual(options["password"], "p:ss")
            self.assertEqual(options["database"], "my db")
            self.assertEqual(options["port"], 3307)
        self.assertEqual(db.connection_options({"DATABASE_URL": "mysql://reader@[::1]/app"})["host"], "::1")

    def test_bad_configuration_does_not_leak(self):
        cases = [{}, {**ENV, "DB_PORT": "secret"}, {**ENV, "DB_PORT": "0"},
                 {**ENV, "DB_PORT": "65536"}, {**ENV, "DB_HOST": ""}]
        cases += [{"DATABASE_URL": url} for url in ["postgres://u:secret@host/db", "mysql://host/db",
                   "mysql://u:secret@host", "mysql://u:secret@host:bad/db",
                   "mysql://u:secret@host/db?local_infile=1", "mysql://u:secret@host/db#secret"]]
        for env in cases:
            with self.subTest(env=env), self.assertRaises(db.DatabaseError) as ctx:
                db.connection_options(env)
            self.assertEqual(str(ctx.exception), "DB_CONFIG_ERROR")


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.cursor.description = [("id",), ("name",)]
        self.cursor.fetchmany.return_value = [(1, "one"), (2, "two")]
        self.driver = SimpleNamespace(connect=MagicMock(return_value=self.connection),
                                      cursors=SimpleNamespace(SSCursor=object()))
        self.loader = patch.object(db.importlib, "import_module", return_value=self.driver)
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def query(self, sql="SELECT id, name FROM users", params=None, **kwargs):
        return db.query_read_only(sql, params, environ=ENV, **kwargs)

    def test_read_only_transaction_params_and_cleanup(self):
        result = self.query("SELECT id, name FROM users WHERE id=%s", [1], timeout=2.5, max_rows=10)
        self.assertEqual(result, {"rows": [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}],
                                  "fields": ["id", "name"], "row_count": 2, "truncated": False})
        self.assertEqual(self.cursor.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))
        self.assertEqual(self.cursor.execute.call_args_list[1].args, ("START TRANSACTION READ ONLY",))
        self.assertEqual(self.cursor.execute.call_args_list[2].args,
                         ("SELECT id, name FROM users WHERE id=%s", (1,)))
        self.cursor.fetchmany.assert_called_once_with(11)
        options = self.driver.connect.call_args.kwargs
        for key in ["connect_timeout", "read_timeout", "write_timeout"]:
            self.assertEqual(options[key], 2.5)
        self.assertFalse(options["autocommit"])
        self.assertFalse(options["local_infile"])
        self.assertIs(options["cursorclass"], self.driver.cursors.SSCursor)
        self.assertNotIn("client_flag", options)
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.cursor.close.assert_called_once()

    def test_truncation_and_order_of_transport_abort(self):
        result = self.query(max_rows=1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(len(result["rows"]), 1)
        methods = [call[0] for call in self.connection.mock_calls]
        self.assertLess(methods.index("close"), methods.index("rollback"))
        self.assertLess(methods.index("close"), methods.index("cursor().close"))
        self.cursor.fetchmany.assert_called_once_with(2)

    def test_empty_and_exact_limit(self):
        for rows, size in [([], 1), ([(1, "one")], 1)]:
            with self.subTest(rows=rows):
                self.cursor.fetchmany.return_value = rows
                result = self.query(max_rows=size)
                self.assertFalse(result["truncated"])
                self.assertEqual(result["row_count"], len(rows))

    def test_fail_closed_at_both_transaction_statements(self):
        for position in [0, 1, 2]:
            with self.subTest(position=position):
                self.cursor.reset_mock()
                self.connection.reset_mock()
                self.cursor.execute.side_effect = [None] * position + [RuntimeError("secret SQL")]
                with self.assertRaises(db.DatabaseError) as ctx:
                    self.query()
                self.assertEqual(str(ctx.exception), "DB_QUERY_FAILED")
                self.assertEqual(self.cursor.execute.call_count, position + 1)
                self.cursor.fetchmany.assert_not_called()
                self.connection.rollback.assert_called_once()
                self.connection.close.assert_called()

    def test_connect_and_fetch_errors_are_safe(self):
        self.driver.connect.side_effect = RuntimeError("password secret SELECT private")
        with self.assertRaisesRegex(db.DatabaseError, "^DB_CONNECTION_FAILED$"):
            self.query()
        self.connection.rollback.assert_not_called()
        self.driver.connect.side_effect = None
        self.cursor.fetchmany.side_effect = RuntimeError("SQL secret")
        with self.assertRaisesRegex(db.DatabaseError, "^DB_QUERY_FAILED$"):
            self.query()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called()

    def test_result_shape_and_duplicates(self):
        for description, code in [(None, "DB_UNEXPECTED_RESULT"), ([("x",), ("x",)], "DB_DUPLICATE_COLUMNS")]:
            with self.subTest(description=description):
                self.cursor.description = description
                with self.assertRaisesRegex(db.DatabaseError, "^" + code + "$"):
                    self.query()
        self.cursor.fetchmany.assert_not_called()

    def test_cleanup_failures_still_close(self):
        self.connection.rollback.side_effect = RuntimeError("secret")
        with self.assertRaisesRegex(db.DatabaseError, "^DB_CLEANUP_FAILED$"):
            self.query()
        self.connection.close.assert_called()
        self.connection.rollback.side_effect = None
        self.connection.close.side_effect = RuntimeError("secret")
        with self.assertRaisesRegex(db.DatabaseError, "^DB_CLEANUP_FAILED$"):
            self.query()

    def test_missing_driver_and_preconnect_validation(self):
        with patch.object(db.importlib, "import_module", side_effect=ImportError("secret")):
            with self.assertRaisesRegex(db.DatabaseError, "^DB_DRIVER_UNAVAILABLE$"):
                self.query()
        for kwargs in [{"max_rows": 0}, {"max_rows": True}, {"max_rows": 100001}, {"max_rows": 1.5},
                       {"timeout": 0}, {"timeout": -1}, {"timeout": float("nan")},
                       {"timeout": float("inf")}, {"timeout": True}, {"timeout": "10"},
                       {"timeout": 31536001}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(db.DatabaseError):
                self.query(**kwargs)
        with self.assertRaises(db.DatabaseError):
            self.query("DELETE FROM users")
        with self.assertRaises(db.DatabaseError):
            self.query("SELECT %s", [])
        self.driver.connect.assert_not_called()


class CliTests(unittest.TestCase):
    def invoke(self, args, result=None, exception=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, "_load_env_file"), patch.object(cli, "query_read_only", return_value=result,
                     side_effect=exception) as query, redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue(), query

    def test_stdout_and_parameter_json(self):
        result = {"rows": [{"connected": 1}], "fields": ["connected"], "row_count": 1, "truncated": False}
        code, out, err, query = self.invoke(["--sql", "SELECT %s", "--params", "[1]", "--timeout", "5",
                                             "--max-rows", "3", "--env-file", ".env"], result)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), result)
        self.assertEqual(err, "")
        query.assert_called_once_with("SELECT %s", [1], max_rows=3, timeout=5.0)

    def test_file_input_output_and_safe_types(self):
        with tempfile.TemporaryDirectory() as directory:
            sql_file = Path(directory) / "query.sql"
            sql_file.write_text("SELECT 1", encoding="utf-8-sig")
            output = Path(directory) / "nested/result.json"
            result = {"rows": [{"decimal": Decimal("123456789.123456789"), "date": date(2026, 1, 2),
                                "datetime": datetime(2026, 1, 2, 3, 4), "time": time(3, 4),
                                "duration": timedelta(hours=30), "blob": b"\xff\x00"}],
                      "fields": [], "row_count": 1, "truncated": False}
            code, out, err, query = self.invoke(["--sql-file", str(sql_file), "--output", str(output)], result)
            self.assertEqual((code, out, err), (0, "", ""))
            row = json.loads(output.read_text(encoding="utf-8"))["rows"][0]
            self.assertEqual(row["decimal"], "123456789.123456789")
            self.assertEqual(row["date"], "2026-01-02")
            self.assertEqual(row["blob"], {"encoding": "base64", "data": "/wA="})
            query.assert_called_once_with("SELECT 1", [], max_rows=100, timeout=10)

    def test_failures_never_echo_sql_credentials_or_exception(self):
        cases = [(["--sql", "SELECT secret"], RuntimeError("password=secret SELECT private"), "DB_QUERY_FAILED"),
                 (["--sql", "SELECT secret"], db.DatabaseError("DB_CONNECTION_FAILED"), "DB_CONNECTION_FAILED"),
                 (["--sql", "SELECT secret"], db.DatabaseError("secret"), "DB_QUERY_FAILED"),
                 (["--sql", "SELECT secret", "--params", "secret"], None, "DB_INVALID_PARAMS"),
                 (["--sql", "SELECT secret", "--params", "[NaN]"], None, "DB_INVALID_PARAMS"),
                 (["--sql", "SELECT secret", "--timeout", "secret"], None, "DB_INVALID_ARGUMENT"),
                 (["--sql", "SELECT secret", "--other-secret"], None, "DB_INVALID_ARGUMENT"),
                 (["--sql", "SELECT secret", "--sql-file", "secret"], None, "DB_INVALID_ARGUMENT"),
                 ([], None, "DB_INVALID_ARGUMENT"),
                 (["--sql-file", "nonexistent-secret.sql"], None, "DB_INPUT_ERROR")]
        for args, exc, category in cases:
            with self.subTest(args=args):
                code, out, err, _ = self.invoke(args, exception=exc)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, category + "\n")

    def test_output_cannot_overwrite_inputs(self):
        for args in [["--sql", "SELECT 1", "--output", ".env"],
                     ["--sql", "SELECT 1", "--env-file", "secrets", "--output", "secrets"],
                     ["--sql-file", "query.sql", "--output", "query.sql"],
                     ["--sql", "SELECT 1", "--output", "nested/.env"]]:
            with self.subTest(args=args):
                code, out, err, query = self.invoke(args)
                self.assertEqual((code, out, err), (2, "", "DB_INVALID_ARGUMENT\n"))
                query.assert_not_called()

    def test_env_and_output_failures(self):
        with patch.object(cli, "_load_env_file", side_effect=ValueError("secret env")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(cli.main(["--sql", "SELECT 1"]), 2)
            self.assertEqual(stderr.getvalue(), "DB_CONFIG_ERROR\n")
        code, out, err, _ = self.invoke(["--sql", "SELECT 1"], {"unsupported": object()})
        self.assertEqual((code, out, err), (2, "", "DB_OUTPUT_ERROR\n"))


if __name__ == "__main__":
    unittest.main()
