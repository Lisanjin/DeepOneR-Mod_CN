"""Prepare the verified vc32 build inputs and select the release snapshot."""
import hashlib
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from refresh_upstream import download

ROOT = Path(__file__).resolve().parent
UPSTREAM = 'https://raw.githubusercontent.com/Lisanjin/DeepOne_translate_CN'
AOSP = 'https://raw.githubusercontent.com/aosp-mirror/platform_build/android-14.0.0_r1/target/product/security'


def replace_file(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(body)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_file(relative, url, digest):
    path = ROOT / 'vendor' / relative
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return
    print('下载 ' + relative, flush=True)
    body = download(url)
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError('下载内容与固定 SHA-256 不符：' + relative)
    replace_file(path, body)


def prepare():
    pinned = (ROOT / 'vendor/pinned.json').read_bytes()
    record = json.loads(pinned)
    commit = record['commit']
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    prepare_file('Humming-D.ttf', f'{UPSTREAM}/{commit}/assets/Humming-D.ttf', config['font_sha256'])
    for name, digest in {
        'testkey.pk8': '495675d32e89a149d5abe191f4e9c0e218b9068714e9b53a7c91e164a0741a23',
        'testkey.x509.pem': 'a4384ba815b9499a5ce349b4e33c1755278873fe2eac150a068823f526e6dbde',
    }.items():
        prepare_file('signing/' + name, f'{AOSP}/{name}', digest)
    for name, info in record['files'].items():
        prepare_file(f'upstream/{commit}/{name}', f'{UPSTREAM}/{commit}/{name}', info['sha256'])
    replace_file(ROOT / 'vendor/upstream/current.json', pinned)
    print('素材已就绪，已选用 vc32 的补丁快照 ' + commit)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        prepare()
    except (OSError, ValueError) as exc:
        sys.exit('准备素材失败：' + str(exc))
