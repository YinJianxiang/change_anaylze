from __future__ import annotations

import unittest

from orchestrator.services.reviewer_router import ReviewerRouter, ReviewerRoutingError


class ReviewerRouterTests(unittest.TestCase):
    def test_resolves_dingtalk_user_id_by_exact_name(self) -> None:
        route = ReviewerRouter({
            "张三": {"dingtalk_user_id": "manager1234", "enabled": True}
        }).resolve("张三")
        self.assertEqual(route.dingtalk_user_id, "manager1234")

    def test_unknown_or_incomplete_route_is_rejected(self) -> None:
        router = ReviewerRouter({"张三": {"dingtalk_user_id": "manager1234"}})
        with self.assertRaises(ReviewerRoutingError):
            router.resolve("李四")
        with self.assertRaises(ReviewerRoutingError):
            ReviewerRouter({"李四": {"email": "li@example.test"}}).resolve("李四")


if __name__ == "__main__":
    unittest.main()
