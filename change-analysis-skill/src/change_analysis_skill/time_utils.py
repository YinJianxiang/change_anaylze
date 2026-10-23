from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def now_beijing() -> str:
    return datetime.now(BEIJING_TZ).isoformat()
