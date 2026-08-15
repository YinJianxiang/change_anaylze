from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "analyze-change-test-scope" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_repository_impact as impact  # noqa: E402
import prepare_pr_workspace as prepare  # noqa: E402


def git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return process.stdout.strip()


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class TemporaryRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Skill Test")
        git(self.repo, "config", "user.email", "skill@example.test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, message: str) -> str:
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")


class PrepareWorkspaceTests(TemporaryRepository):
    def test_parse_github_pr_url(self) -> None:
        self.assertEqual(
            prepare.parse_pr_url("https://github.com/acme/shop/pull/42"),
            ("acme", "shop", 42),
        )

    def test_rejects_non_github_pr_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "GitHub pull request URL"):
            prepare.parse_pr_url("https://gitlab.com/acme/shop/merge_requests/42")

    def _repository_info(self) -> tuple[prepare.PullRequestInfo, str, str]:
        write(self.repo / "app.py", "value = 1\n")
        base = self.commit("base")
        git(self.repo, "switch", "-c", "feature")
        write(self.repo / "app.py", "value = 2\n")
        head = self.commit("head")
        git(self.repo, "switch", "main")
        git(self.repo, "remote", "add", "origin", "https://github.com/acme/shop.git")
        return (
            prepare.PullRequestInfo("acme", "shop", 7, "main", "feature", base, head, "test"),
            base,
            head,
        )

    def test_existing_repository_is_not_cloned_and_branch_is_unchanged(self) -> None:
        info, _, _ = self._repository_info()
        before = git(self.repo, "branch", "--show-current")
        original_runner = prepare.run_command
        commands: list[list[str]] = []

        def recording_runner(args: list[str], **kwargs: object) -> str:
            commands.append(args)
            return original_runner(args, **kwargs)

        with mock.patch.object(prepare, "_fetch_pr_refs"), mock.patch.object(
            prepare, "run_command", side_effect=recording_runner
        ):
            result = prepare.prepare_workspace(
                "https://github.com/acme/shop/pull/7",
                repo_path=self.repo,
                cache_dir=Path(self.temp.name) / "cache",
                info_provider=lambda _url, _timeout: info,
            )
        self.assertEqual(result["repository_source"], "existing-local-repository")
        self.assertFalse(any(command[:2] == ["git", "clone"] for command in commands))
        self.assertEqual(git(self.repo, "branch", "--show-current"), before)
        self.assertTrue(result["source_repository_branch_unchanged"])

    def test_resolves_base_head_and_merge_base(self) -> None:
        info, base, head = self._repository_info()
        with mock.patch.object(prepare, "_fetch_pr_refs"):
            result = prepare.prepare_workspace(
                "https://github.com/acme/shop/pull/7",
                repo_path=self.repo,
                cache_dir=Path(self.temp.name) / "cache",
                info_provider=lambda _url, _timeout: info,
            )
        self.assertEqual(result["base_sha"], base)
        self.assertEqual(result["head_sha"], head)
        self.assertEqual(result["merge_base"], base)
        self.assertEqual(result["range"], f"{base}...{head}")


class RepositoryImpactTests(TemporaryRepository):
    def setUp(self) -> None:
        super().setUp()
        write(
            self.repo / "backend" / "service.py",
            "class OrderService:\n"
            "    def calculate_amount(self, order):\n"
            "        return order.subtotal\n\n"
            "def create_order(order):\n"
            "    return OrderService().calculate_amount(order)\n",
        )
        write(
            self.repo / "backend" / "routes.py",
            "from backend.service import create_order\n\n"
            "def list_orders():\n"
            "    return create_order({})\n",
        )
        write(self.repo / "frontend" / "api.ts", "export const loadOrders = () => fetch('/api/orders');\n")
        write(
            self.repo / "frontend" / "page.ts",
            "import { loadOrders } from './api';\n\n"
            "class OrderPage {\n"
            "  render() {\n"
            "    return loadOrders();\n"
            "  }\n"
            "}\n",
        )
        write(
            self.repo / "tests" / "test_service.py",
            "from backend.service import OrderService\n\ndef test_amount():\n    OrderService().calculate_amount(object())\n",
        )
        write(self.repo / "node_modules" / "ignored.js", "calculate_amount(); fetch('/api/orders');\n")
        self.base = self.commit("base")
        write(
            self.repo / "backend" / "service.py",
            "class OrderService:\n"
            "    def calculate_amount(self, order):\n"
            "        return max(order.subtotal, 0)\n\n"
            "def create_order(order):\n"
            "    return OrderService().calculate_amount(order)\n",
        )
        write(
            self.repo / "backend" / "routes.py",
            "from backend.service import create_order\n\n"
            "@app.get('/api/orders')\n"
            "def list_orders():\n"
            "    return create_order({})\n",
        )
        self.head = self.commit("head")
        self.result = impact.collect_impact(self.repo, f"{self.base}...{self.head}")

    def test_extracts_changed_python_function_and_class(self) -> None:
        names = {(item["symbol"], item["kind"]) for item in self.result["changed_symbols"]}
        self.assertIn(("calculate_amount", "function"), names)
        self.assertIn(("OrderService", "class"), names)

    def test_finds_other_function_calls_with_line_evidence(self) -> None:
        matches = [item for item in self.result["symbol_references"] if item["symbol"] == "calculate_amount"]
        self.assertTrue(any(item["file"] == "backend/service.py" and item["line"] > 0 for item in matches))
        self.assertTrue(all(item["evidence"] for item in matches))

    def test_finds_frontend_api_consumer(self) -> None:
        self.assertTrue(
            any(item["file"] == "frontend/api.ts" and item["symbol"] == "/api/orders" for item in self.result["frontend_consumers"])
        )

    def test_finds_related_tests(self) -> None:
        self.assertTrue(any(item["file"] == "tests/test_service.py" for item in self.result["related_tests"]))

    def test_scans_all_supported_project_source_files(self) -> None:
        scan = self.result["repository_scan"]
        self.assertEqual(scan["candidate_source_files"], 5)
        self.assertEqual(scan["source_files_scanned"], 5)
        self.assertTrue(scan["scan_complete"])

    def test_builds_lightweight_direct_and_transitive_call_paths(self) -> None:
        graph = self.result["lightweight_call_graph"]
        nodes = {item["id"]: item for item in graph["nodes"]}
        edges = graph["edges"]
        self.assertTrue(
            any(
                nodes.get(edge["caller"], {}).get("symbol") == "create_order"
                and edge["callee_symbol"] == "calculate_amount"
                and edge["resolved_callees"]
                for edge in edges
            )
        )
        self.assertTrue(
            any(
                nodes.get(edge["caller"], {}).get("symbol") == "render"
                and edge["callee_symbol"] == "loadOrders"
                and edge["resolved_callees"]
                for edge in edges
            )
        )
        self.assertTrue(
            any(
                [item["symbol"] for item in path["path"]]
                == ["list_orders", "create_order", "calculate_amount"]
                for path in graph["impact_paths"]
            )
        )

    def test_excludes_dependency_and_build_directories(self) -> None:
        serialized = json.dumps(self.result)
        self.assertNotIn("node_modules/ignored.js", serialized)

    def test_output_is_stable_json(self) -> None:
        encoded = json.dumps(self.result, sort_keys=True)
        self.assertEqual(json.loads(encoded)["analysis_mode"], "Full repository context analysis")

    def test_change_context_reports_structured_files_and_revisions(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "collect_change_context.py"),
                "--repo",
                str(self.repo),
                "--range",
                f"{self.base}...{self.head}",
                "--analysis-mode",
                "full",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        payload = json.loads(process.stdout)
        self.assertEqual(payload["base_sha"], self.base)
        self.assertEqual(payload["head_sha"], self.head)
        self.assertTrue(any(item["path"] == "backend/service.py" for item in payload["changed_files"]))


class CommandLineFallbackTests(unittest.TestCase):
    def test_impact_cli_degrades_to_diff_only_json_without_repository(self) -> None:
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "collect_repository_impact.py"),
                "--repo",
                "/path/that/does/not/exist",
                "--range",
                "main...HEAD",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        payload = json.loads(process.stderr)
        self.assertEqual(payload["analysis_mode"], "Diff-only analysis")
        self.assertIn("未获取完整仓库", payload["analysis_limits"][0])

    def test_prepare_cli_reports_invalid_url_as_json(self) -> None:
        process = subprocess.run(
            [sys.executable, str(SCRIPTS / "prepare_pr_workspace.py"), "--pr-url", "https://example.com/pr/1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stderr)["analysis_mode"], "Diff-only analysis")


if __name__ == "__main__":
    unittest.main()
