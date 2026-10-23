"""Fresh remote commits and configured repositories are mandatory at the CLI."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from change_analysis_skill.skill import (
    ChangeAnalysisSkill, ChangeAnalysisRequest, SkillInputError, _repository_path, main,
)


class RepositoryConfigurationTests(unittest.TestCase):
    def test_cli_reads_named_path_and_base_branch_from_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / '.env'
            env_file.write_text('SKILL_REPOSITORIES=\'{"backend":"checkouts/backend"}\'\nSKILL_BASE_BRANCH=main\n', encoding='utf-8')
            result = {'analysis_input': {}}
            with patch.dict(os.environ, {}, clear=True), patch.object(ChangeAnalysisSkill, 'run', return_value=result) as run, redirect_stdout(io.StringIO()):
                main(['https://example.invalid/1', 'feature', '--repository-name', 'backend', '--env-file', str(env_file), '--no-report', '--no-archify'])
            request = run.call_args.args[0]
            self.assertEqual(request.repository, (root / 'checkouts/backend').resolve())
            self.assertEqual(request.base_branch, 'main')

    def test_explicit_path_overrides_env_and_unknown_name_fails(self):
        with patch.dict(os.environ, {'SKILL_REPO': '/default', 'SKILL_REPOSITORIES': '{"backend":"/configured"}'}, clear=True):
            self.assertEqual(_repository_path(Path('explicit'), 'backend', Path('.env')), Path('explicit').resolve())
            self.assertEqual(_repository_path(None, 'repository', Path('.env')), Path('/default').resolve())
            with self.assertRaisesRegex(SkillInputError, 'not configured'):
                _repository_path(None, 'unknown', Path('.env'))

    def test_invalid_mapping_reports_configuration_error(self):
        for value in ('invalid', '[]', '{"backend":null}', '{"backend":" "}'):
            with self.subTest(value=value), patch.dict(os.environ, {'SKILL_REPOSITORIES': value}, clear=True):
                with self.assertRaisesRegex(SkillInputError, 'SKILL_REPOSITORIES must be'):
                    _repository_path(None, 'backend', Path('.env'))

    def test_skip_fetch_is_rejected(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            main(['https://example.invalid/1', 'feature', '--no-fetch'])
        self.assertEqual(failure.exception.code, 2)


class RepositoryRefreshTests(unittest.TestCase):
    def test_repeated_runs_refresh_restricted_refs_and_preserve_worktree(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GIT_PERSONAL_ACCESS_TOKEN': '', 'GIT_AUTH_HEADER': ''}):
            root = Path(directory)
            writer, client, remote = (root / name for name in ('writer', 'client', 'remote.git'))

            def git(repo, *args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE, text=True, encoding='utf-8').strip()

            writer.mkdir()
            git(writer, 'init', '-b', 'main')
            git(writer, 'config', 'user.name', 'Refresh Test')
            git(writer, 'config', 'user.email', 'refresh@example.invalid')
            source = writer / 'value.txt'
            source.write_text('base\n', encoding='utf-8')
            git(writer, 'add', '.')
            git(writer, 'commit', '-m', 'base')
            base = git(writer, 'rev-parse', 'HEAD')
            git(root, 'init', '--bare', str(remote))
            git(writer, 'remote', 'add', 'origin', str(remote))
            git(writer, 'push', 'origin', 'main')
            git(root, 'clone', '--branch', 'main', '--single-branch', str(remote), str(client))
            # Client origin.fetch intentionally only includes main.
            git(writer, 'checkout', '-b', 'feature')
            source.write_text('first\n', encoding='utf-8')
            git(writer, 'commit', '-am', 'first')
            git(writer, 'push', 'origin', 'feature')
            (client / 'value.txt').write_text('local edit\n', encoding='utf-8')
            request = ChangeAnalysisRequest('https://example.invalid/1', 'feature', client, 'main')
            requirement = Mock()
            requirement.prepare.return_value = {'documents': []}
            skill = ChangeAnalysisSkill(requirement_service=requirement)

            def enrich(snapshots, **kwargs):
                return snapshots, 'Diff-only evidence analysis', []

            with patch('change_analysis_skill.skill.enrich_snapshots', side_effect=enrich):
                first = skill.run(request)['repository']
                self.assertEqual(first['target_commit'], git(writer, 'rev-parse', 'HEAD'))
                # Rewrite the remote feature history; the next run must refresh it.
                git(writer, 'reset', '--hard', base)
                source.write_text('rewritten\n', encoding='utf-8')
                git(writer, 'commit', '-am', 'rewrite')
                git(writer, 'push', '--force', 'origin', 'feature')
                second = skill.run(request)['repository']
            self.assertNotEqual(first['target_commit'], second['target_commit'])
            self.assertEqual(second['target_commit'], git(writer, 'rev-parse', 'HEAD'))
            self.assertIn('+rewritten', second['diff'])
            self.assertEqual(git(client, 'branch', '--show-current'), 'main')
            self.assertEqual((client / 'value.txt').read_text(encoding='utf-8'), 'local edit\n')
            # A failed fetch must not proceed using the previously fetched feature.
            git(writer, 'push', 'origin', '--delete', 'feature')
            requirement.reset_mock()
            with self.assertRaises(RuntimeError):
                skill.run(request)
            requirement.prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
