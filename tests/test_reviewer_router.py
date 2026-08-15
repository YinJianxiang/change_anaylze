from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.services.reviewer_router import ReviewerRouter, ReviewerRoutingError


class ReviewerRouterTests(unittest.TestCase):
    def test_resolves_open_id_by_exact_name(self) -> None:
        router = ReviewerRouter({"张三": {"open_id": "ou_123", "enabled": True}})
        route = router.resolve("张三")
        self.assertEqual((route.receive_id_type, route.receive_id), ("open_id", "ou_123"))

    def test_unknown_name_does_not_fall_back_when_configured(self) -> None:
        router = ReviewerRouter({"张三": {"open_id": "ou_123"}})
        with self.assertRaises(ReviewerRoutingError):
            router.resolve("李四")

    def test_empty_config_uses_personal_default(self) -> None:
        with patch.dict("os.environ", {"FEISHU_RECEIVE_ID_TYPE": "email", "FEISHU_RECEIVE_ID": "me@example.com"}):
            route = ReviewerRouter().resolve("自己")
        self.assertEqual((route.receive_id_type, route.receive_id), ("email", "me@example.com"))


if __name__ == "__main__":
    unittest.main()
