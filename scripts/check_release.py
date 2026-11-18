#!/usr/bin/env python3
"""Reject common private artifacts and credential formats without printing values."""
import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = (
    '.env', '.env.*', '*.key', '*.pem', '*.p12', '*.pfx', 'id_rsa*', 'id_ed25519*',
    '*cookie*.json', '*cookie*.txt', '*storage_state*.json', '*storage-state*.json',
    '*storagestate*.json', '*credential*.json', '*secret*.json', '*.har',
    '*.db', '*.db-*', '*.sqlite', '*.sqlite-*', '*.sqlite3', '*.sqlite3-*',
    '*.log', '*.zip', '*.zip.sha256', '*.mp4', '*.mp3', '*.m4a', '*.webm',
)
PRIVATE_DIRS = {'data', 'backups', 'exports', 'downloads', 'artifacts',
                'playwright-report', 'test-results', '__pycache__', 'venv'}
SECRET_PATTERNS = (
    ('private key', re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----')),
    ('provider token', re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b')),
    ('AWS access key', re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('session cookie', re.compile(rb'(?i)\b(?:sessionid(?:_ss)?|sid_guard)["\x27]?\s*[=:]\s*["\x27]?[A-Za-z0-9%_/-]{24,}')),
)


def problems(name, data):
    path = PurePosixPath(name)
    lower = path.name.lower()
    result = []
    if any(part.lower() in PRIVATE_DIRS or part.lower().startswith(('.venv', 'browser-profile'))
           for part in path.parts[:-1]):
        result.append('private runtime directory')
    if lower != '.env.example' and any(fnmatch.fnmatchcase(lower, pattern) for pattern in PRIVATE_NAMES):
        result.append('private/runtime filename')
    if lower == '.env.example':
        # Being an example filename must not make a real administrator password safe.
        for line in data.decode('utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator and re.search(r'(?:PASSWORD|TOKEN|SECRET|KEY)$', key.strip()):
                if value.strip().strip('"\x27') not in ('', 'replace-with-a-random-password-at-least-10-characters'):
                    result.append('non-placeholder credential in environment example')
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(data):
            result.append(label)
    return result


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.PIPE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='Scan the entire Git index, not working-tree files')
    args = parser.parse_args()
    failures = []
    if args.staged:
        # The index is the release boundary; even previously tracked private files fail.
        entries = git('ls-files', '--stage', '-z').split(b'\0')
        records = []
        for entry in filter(None, entries):
            metadata, raw_name = entry.split(b'\t', 1)
            mode, oid, stage = metadata.decode().split()
            name = raw_name.decode('utf-8', errors='surrogateescape')
            if mode != '100644' and mode != '100755':
                failures.append((name, ['symlink, submodule or unresolved index entry']))
                continue
            if stage != '0':
                failures.append((name, ['unresolved index entry']))
                continue
            records.append((name, git('cat-file', 'blob', oid)))
    else:
        from package_source import source_contents
        records = source_contents().items()
    for name, data in records:
        issues = problems(name, data)
        if issues:
            failures.append((name, issues))
    if failures:
        for name, issues in failures:
            print(f'BLOCKED {ascii(name)}: {", ".join(issues)}', file=sys.stderr)
        return 1
    print('PASS: checked source files; no matched private artifacts or credential patterns')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # Do not print subprocess output or file contents: they may contain secrets.
        print(f'Release check failed ({type(error).__name__}); check paths and Git index.', file=sys.stderr)
        sys.exit(1)
