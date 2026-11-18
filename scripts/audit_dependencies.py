#!/usr/bin/env python3
"""Check every locked Python version against public PyPI advisory metadata.

Sends only package names/versions. Network errors fail the audit. This is a
point-in-time advisory check, not a proof that dependencies have no defects.
"""
import argparse
import concurrent.futures
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def locked_packages(text):
    packages = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(('#', '--hash=')):
            continue
        match = re.match(r'^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?:\s|$)', line)
        if not match:
            raise ValueError('Expected fully pinned requirements with hashes')
        packages.append(match.groups())
    if not packages:
        raise ValueError('Empty lock file')
    return packages


def check(package):
    name, version = package
    result = {'package': name, 'version': version}
    try:
        url = f'https://pypi.org/pypi/{name}/{version}/json'
        request = urllib.request.Request(url, headers={'User-Agent': 'douyin-vault-dependency-audit'})
        with urllib.request.urlopen(request, timeout=15) as response:
            metadata = json.load(response)
        # A withdrawn advisory is retained in PyPI but is no longer an active finding.
        result['advisories'] = [
            {key: advisory.get(key) for key in ('id', 'aliases', 'fixed_in', 'link')}
            for advisory in metadata['vulnerabilities'] if not advisory.get('withdrawn')
        ]
        result['license'] = metadata['info'].get('license_expression') or metadata['info'].get('license')
        result['source'] = url
    except (OSError, ValueError, KeyError) as error:
        result['error'] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='Optional local JSON report; review before publishing')
    args = parser.parse_args()
    packages = locked_packages((ROOT / 'requirements.txt').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(check, packages))
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'source': 'PyPI version JSON vulnerabilities field', 'packages': results}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    failed = False
    for item in results:
        if item.get('error'):
            print(f'ERROR {item["package"]}=={item["version"]}: {item["error"]}')
            failed = True
        elif item['advisories']:
            print(f'VULNERABLE {item["package"]}=={item["version"]}: ' +
                  ', '.join(str(a['id']) for a in item['advisories']))
            failed = True
    if not failed:
        print(f'PASS: {len(results)} locked packages; no active advisories reported by PyPI')
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
