#!/usr/bin/env python3
"""Package an independent source checkout with per-file and archive checksums."""
import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path
from check_release import problems

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ('README.md', 'HERMES.md', 'VALIDATION.md', 'THIRD_PARTY.md', 'LICENSE',
              'Dockerfile', 'compose.yaml', 'requirements.in', 'requirements.txt',
              '.env.example', '.gitignore', '.dockerignore', '.githooks/pre-commit')
# New files require an explicit release review; an unexpected JSON or media file
# inside app/tests/scripts/deploy must never silently enter a source archive.
SOURCE_FILES = (
    'app/__init__.py', 'app/main.py', 'app/config.py', 'app/db.py',
    'app/security.py', 'app/network.py', 'app/browser.py', 'app/collector.py',
    'app/parser.py', 'app/storage.py', 'app/worker.py',
    'app/static/app.js', 'app/static/content.js', 'app/static/gallery.js',
    'app/static/base.css', 'app/static/style.css', 'app/static/index.html',
    'app/static/logo.svg', 'app/static/vendor/markdown-it.min.js',
    'app/static/vendor/markdown-it-source.json',
    'app/static/vendor/MARKDOWN-IT-LICENSE',
    'app/static/vendor/THIRD-PARTY-NOTICES.txt',
    'tests/test_vault.py', 'tests/test_content.cjs', 'tests/test_login_poll.cjs',
    'tests/browser_login_smoke.py', 'tests/test_release.py',
    'scripts/setup_env.py', 'scripts/doctor.py', 'scripts/package_source.py',
    'scripts/check_release.py', 'scripts/audit_dependencies.py',
    'deploy/PLAYWRIGHT-LICENSE', 'deploy/seccomp_profile.json',
    'deploy/nginx.conf.example',
)
OPTIONAL_FILES = ('NOTICE', 'NOTICE.txt')


def source_contents():
    files = [ROOT / name for name in (*ROOT_FILES, *SOURCE_FILES)]
    files += [ROOT / name for name in OPTIONAL_FILES if (ROOT / name).exists()]
    contents = {}
    for path in sorted(files):
        if (any(parent.is_symlink() for parent in (path, *path.parents)
                if parent == ROOT or ROOT in parent.parents)
                or not path.resolve().is_relative_to(ROOT.resolve())):
            raise ValueError('Source files must be regular files inside the checkout')
        name = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        if problems(name, data):
            raise ValueError(f'Private data detected in {name}; package aborted')
        contents[name] = data
    return contents


def package(destination):
    contents = source_contents()
    manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in contents.items())
    destination = destination.absolute()
    if destination.suffix != '.zip':
        raise ValueError('Output must be a .zip file')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.vault-package-', dir=destination.parent) as tmp:
        staged = Path(tmp) / 'source.zip'
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in contents.items():
                entry = zipfile.ZipInfo('shiying-vault/' + name)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (0o100755 if name == '.githooks/pre-commit' else 0o100644) << 16
                archive.writestr(entry, data)
            archive.writestr('shiying-vault/SOURCE_MANIFEST.sha256', manifest)
        with zipfile.ZipFile(staged) as archive:
            assert archive.testzip() is None
        staged.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n')
    print(f'Source archive: {destination}')
    print(f'{len(contents)} source files + manifest; {destination.stat().st_size} bytes')
    print(f'SHA-256: {digest}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'shiying-vault-source.zip')
    package(parser.parse_args().output)
