"""Load .env then start the DingTalk MCP bridge."""
from __future__ import annotations

import os
import runpy
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    os.chdir(root)
    # Ensure stdout is line-buffered when started as subprocess.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    import dingtalk_doc_bridge.__main__ as bridge_main

    # Mimic: python -m dingtalk_doc_bridge --host 127.0.0.1 --port 8091
    import sys

    sys.argv = ["dingtalk_doc_bridge", "--host", "127.0.0.1", "--port", "8091"]
    bridge_main.main()
