"""Release boundary regressions use synthetic private artifacts only."""
import contextlib
import hashlib
import io
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import package_source
from check_release import problems
from audit_dependencies import locked_packages


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vault-release-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in (*package_source.ROOT_FILES, *package_source.SOURCE_FILES):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, target)
        self.patcher = patch.object(package_source, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_archive_includes_notices_lock_license_and_checksums(self):
        output = self.root / 'release.zip'
        with contextlib.redirect_stdout(io.StringIO()):
            package_source.package(output)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertEqual(archive.read('shiying-vault/LICENSE'), (REPO / 'LICENSE').read_bytes())
            for name in ('LICENSE', 'requirements.in', 'requirements.txt', '.githooks/pre-commit',
                         'app/static/vendor/THIRD-PARTY-NOTICES.txt'):
                self.assertIn('shiying-vault/' + name, names)
            self.assertTrue(archive.getinfo('shiying-vault/.githooks/pre-commit').external_attr >> 16 & 0o111)
            for line in archive.read('shiying-vault/SOURCE_MANIFEST.sha256').decode().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256(archive.read('shiying-vault/' + name)).hexdigest(), digest)
        self.assertEqual(output.with_suffix('.zip.sha256').read_text().split()[0],
                         hashlib.sha256(output.read_bytes()).hexdigest())

    def test_unknown_and_private_files_never_enter_archive(self):
        private_fixture = secrets.token_hex(24)
        for name in ('.env', 'app/cookies.json', 'tests/storage_state.json', 'app/unreviewed.json',
                     'deploy/encryption.key', 'data/vault.db', 'app/recording.mp4'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(private_fixture)
        contents = package_source.source_contents()
        self.assertNotIn('app/unreviewed.json', contents)
        self.assertFalse(any(problems(name, data) for name, data in contents.items()))
        self.assertFalse(any(private_fixture.encode() in data for data in contents.values()))

    def test_secret_in_approved_file_blocks_and_preserves_previous_archive(self):
        (self.root / 'README.md').write_text('gh' + 'p_' + 'A' * 36)
        output = self.root / 'release.zip'
        output.write_bytes(b'previous archive')
        with self.assertRaisesRegex(ValueError, 'Private data'):
            package_source.package(output)
        self.assertEqual(output.read_bytes(), b'previous archive')

    def test_symlink_file_and_directory_cannot_enter_archive(self):
        target = self.root / 'app/config.py'
        target.unlink()
        target.symlink_to(self.root / 'README.md')
        with self.assertRaises(ValueError):
            package_source.source_contents()
        target.unlink()
        shutil.copy2(REPO / 'app/config.py', target)
        original = self.root / 'app'
        original.rename(self.root / 'other-app')
        original.symlink_to(self.root / 'other-app', target_is_directory=True)
        with self.assertRaises(ValueError):
            package_source.source_contents()

    def test_missing_required_notice_fails_release(self):
        for name in ('LICENSE', 'app/static/vendor/THIRD-PARTY-NOTICES.txt'):
            with self.subTest(name=name):
                (self.root / name).unlink()
                with self.assertRaises(FileNotFoundError):
                    package_source.source_contents()
                shutil.copy2(REPO / name, self.root / name)

    def test_private_names_and_secret_patterns(self):
        for name in ('copy/cookies.json', 'app/storageState.json', 'old/vault.db-wal',
                     'deploy/server.pem', 'backups/config.json', '.env.production'):
            self.assertTrue(problems(name, b'fixture'), name)
        self.assertFalse(problems('.env.example', (REPO / '.env.example').read_bytes()))
        self.assertTrue(problems('.env.example', b'ADMIN_PASSWORD=synthetic-real-looking-password'))
        secret = ('-----BEGIN ' + 'PRIVATE KEY-----').encode()
        self.assertTrue(problems('README.md', secret))
        cookie_json = ('{"sessionid":"' + 'C' * 32 + '"}').encode()
        self.assertTrue(problems('example.json', cookie_json))

    def test_lock_is_pinned_and_uses_file_hashes(self):
        text = (REPO / 'requirements.txt').read_text()
        packages = locked_packages(text)
        self.assertGreater(len(packages), 10)
        self.assertGreaterEqual(text.count('--hash=sha256:'), len(packages))
        with self.assertRaises(ValueError):
            locked_packages('fastapi>=0.1')

    @unittest.skipUnless(shutil.which('git'), 'Git is required to test the commit hook')
    def test_index_scan_checks_staged_bytes_even_after_worktree_cleanup(self):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], env=env,
                                  capture_output=True, check=True)
        git('init', '-q')
        git('add', '.')
        # The copied clean source is accepted, including intentional synthetic fixtures.
        def scan():
            return subprocess.run([sys.executable, 'scripts/check_release.py', '--staged'],
                                  cwd=self.root, env=env, capture_output=True)
        self.assertEqual(scan().returncode, 0)
        secret = 'gh' + 'p_' + 'B' * 36
        (self.root / 'README.md').write_text(secret)
        git('add', 'README.md')
        (self.root / 'README.md').write_text('clean working tree, unsafe index')
        result = scan()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(secret.encode(), result.stdout + result.stderr)
        git('add', 'README.md')
        (self.root / 'app/cookies.json').write_text('{}')
        git('add', '-f', 'app/cookies.json')
        self.assertEqual(scan().returncode, 1)


if __name__ == '__main__':
    unittest.main()
