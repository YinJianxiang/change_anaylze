from __future__ import annotations

import json
from pathlib import Path


def load_projects_config(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("projects config must be a JSON object")
    return value
