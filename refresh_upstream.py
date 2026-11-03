"""Refresh the bundled native GitHub patch using one immutable commit."""
import argparse
import datetime
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def download(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'DeepOne-Mod-Builder/1.0'})
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read(20 * 1024 * 1024 + 1)
    if len(body) > 20 * 1024 * 1024:
        raise ValueError('Upstream response exceeds the build limit')
    return body


def refresh(config, ref=None):
    repository = config['github_repository']
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
        raise ValueError('Invalid GitHub repository')
    branch = ref or config['github_branch']
    commit = json.loads(download(f'https://api.github.com/repos/{repository}/commits/{urllib.parse.quote(branch, safe="")}'))['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid GitHub commit SHA')
    captured = {}
    files = {}
    for name in ['assets/src/project.js', 'assets/project.manifest', 'assets/version.manifest']:
        body = download(f'https://raw.githubusercontent.com/{repository}/{commit}/{name}')
        captured[name] = body
        files[name] = dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    manifest = json.loads(captured['assets/project.manifest'])
    version = json.loads(captured['assets/version.manifest'])
    script = captured['assets/src/project.js']
    if manifest['version'] != version['version']:
        raise ValueError('Upstream manifests disagree on the resource version')
    record = manifest['assets']['src/project.js']
    if record['size'] != len(script) or record['md5'] != hashlib.md5(script).hexdigest():
        raise ValueError('Upstream script does not match its manifest')
    if b'ntr.best' in script or b'window.__require' not in script:
        raise ValueError('Upstream does not contain the expected migrated native bundle')
    folder = ROOT / 'vendor/upstream'
    for name, body in captured.items():
        target = folder / commit / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    metadata = dict(repository=repository, commit=commit, version=manifest['version'],
                    capturedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(), files=files)
    temporary = folder / 'current.json.new'
    temporary.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    temporary.replace(folder / 'current.json')
    return metadata


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Branch, tag, or exact commit; defaults to config.json')
    args = parser.parse_args()
    try:
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
        print(json.dumps(refresh(config, args.ref), ensure_ascii=False, indent=2))
    except Exception as exc:
        print('更新补丁快照失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
