"""Document fetch status and process exit codes must reflect usable evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from change_analysis_skill import requirement_cli


class RequirementCliTests(unittest.TestCase):
    def prepare(self, documents):
        return patch.object(requirement_cli.RequirementService, 'prepare', return_value={'documents': documents})

    def test_status_tracks_document_evidence(self):
        cases = [
            ([{'status': 'FETCHED', 'content': '完整正文'}], 'READY_FOR_CODEX'),
            ([{'status': 'FAILED', 'content': None}], 'FAILED'),
            ([], 'FAILED'),
            ([{'status': 'NOT_FETCHED', 'content': None}], 'FAILED'),
            ([{'status': 'FETCHED', 'content': '  '}], 'FAILED'),
            ([{'status': 'UNSUPPORTED', 'content': None}], 'UNSUPPORTED'),
            ([{'status': 'PARTIAL', 'content': '部分正文'}], 'PARTIAL'),
            ([{'status': 'FETCHED', 'content': '正文', 'meta': {'truncated': True}}], 'PARTIAL'),
            ([{'status': 'FETCHED', 'content': '正文', 'meta': {'nested': [{'status': 'FAILED'}]}}], 'PARTIAL'),
            ([{'status': 'FETCHED', 'content': '正文'}, {'status': 'FAILED', 'content': None}], 'PARTIAL'),
        ]
        for documents, expected in cases:
            with self.subTest(expected=expected, documents=documents), self.prepare(documents):
                result = requirement_cli.run('https://example.invalid/story')
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['requirement_documents'], documents)

    def test_cli_writes_failure_evidence_and_returns_nonzero(self):
        for doc, status, code in [
            ({'status': 'FAILED', 'content': None, 'meta': {'error': 'connection refused'}}, 'FAILED', 2),
            ({'status': 'PARTIAL', 'content': '部分正文'}, 'PARTIAL', 1),
            ({'status': 'FETCHED', 'content': '完整正文'}, 'READY_FOR_CODEX', 0),
            ({'status': 'UNSUPPORTED', 'content': None}, 'UNSUPPORTED', 2),
        ]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, self.prepare([doc]), patch.object(requirement_cli, '_load_env_file'):
                output = Path(directory) / 'nested/result.json'
                result = requirement_cli.main(['https://example.invalid/story', '--output', str(output)])
                self.assertEqual(result, code)
                evidence = json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(evidence['status'], status)
                self.assertEqual(evidence['requirement_documents'][0], doc)

    def test_blank_url_is_rejected(self):
        with self.assertRaises(requirement_cli.SkillInputError):
            requirement_cli.run('  ')


if __name__ == '__main__':
    unittest.main()
