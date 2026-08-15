import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator import task_worker


class TaskWorkerMainTests(unittest.TestCase):
    def test_loads_default_env_file_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TASK_WORKER_ENV_TEST=loaded\n", encoding="utf-8")

            with (
                patch.object(task_worker, "DEFAULT_ENV_FILE", env_file),
                patch.object(task_worker, "run_task", return_value=[]),
                patch.object(sys, "argv", ["task_worker"]),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(task_worker.main(), 0)
                self.assertEqual(os.environ["TASK_WORKER_ENV_TEST"], "loaded")


if __name__ == "__main__":
    unittest.main()
