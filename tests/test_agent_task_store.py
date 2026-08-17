import tempfile
import unittest
from pathlib import Path

from orchestrator.storage.agent_task_store import AgentTaskStore


class AgentTaskStoreTests(unittest.TestCase):
    def test_token_scopes_context_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AgentTaskStore(Path(directory) / "tasks.sqlite3")
            token = store.create("task-1", "张三", "manager1234", {"projects": []})
            self.assertIsNone(store.get_context("task-1", "wrong"))
            context = store.get_context("task-1", token)
            self.assertEqual(context["dingtalk_user_id"], "manager1234")
            self.assertTrue(store.complete("task-1", token, {"summary": "done"}))
            self.assertFalse(store.complete("task-1", token, {"summary": "again"}))
            self.assertIsNone(store.get_context("task-1", token))
            store.close()


if __name__ == "__main__":
    unittest.main()
