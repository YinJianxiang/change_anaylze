from __future__ import annotations

import unittest

from orchestrator.services.requirement_service import RequirementService


class RequirementServiceTests(unittest.TestCase):
    def test_preserves_urls_without_claiming_document_fetch(self) -> None:
        result = RequirementService().prepare(["https://docs.example.test/1"])
        self.assertEqual(result["documents"][0]["status"], "NOT_FETCHED")
        self.assertIsNone(result["documents"][0]["content"])
