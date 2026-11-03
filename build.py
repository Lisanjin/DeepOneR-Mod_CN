"""Input one original DeepOneR APK; output a signed, aligned, upgrade-compatible Mod APK."""
import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from lib.android_binary import patch_manifest, patch_resources, enable_debuggable

ROOT = Path(__file__).resolve().parent


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def run(command, log=None):
    result = subprocess.run([str(x) for x in command], capture_output=True, encoding='utf-8', errors='replace')
    text = result.stdout + result.stderr
    if log:
        log.write_text(text, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'命令失败 ({result.returncode})：{command[0]}\n{text}')
    return text


def executable(path):
    return path.with_suffix('.exe') if os.name == 'nt' else path


def find_build_tools(explicit=None):
    if explicit:
        candidates = [Path(explicit).expanduser().resolve()]
    else:
        roots = []
        for name in ['ANDROID_SDK_ROOT', 'ANDROID_HOME']:
            if os.environ.get(name):
                roots.append(Path(os.environ[name]))
        if os.environ.get('LOCALAPPDATA'):
            roots.append(Path(os.environ['LOCALAPPDATA']) / 'Android/Sdk')
        roots += [Path.home() / 'Android/Sdk', Path.home() / 'Library/Android/sdk']
        candidates = []
        for root in roots:
            folder = root / 'build-tools'
            if folder.is_dir():
                candidates.extend(p for p in folder.iterdir() if p.is_dir())
        def score(path):
            numbers = [int(value) for value in re.findall(r'\d+', path.name)]
            return tuple(numbers[:3]), '-' not in path.name
        candidates.sort(key=score, reverse=True)
    for candidate in candidates:
        if all(p.is_file() for p in [executable(candidate / 'aapt'), executable(candidate / 'zipalign'), candidate / 'lib/apksigner.jar']):
            return candidate
    raise ValueError('未找到 Android SDK Build Tools。请用 --build-tools 指定包含 aapt、zipalign 和 lib/apksigner.jar 的目录。')


def metadata(aapt, apk, log=None):
    text = run([aapt, 'dump', 'badging', apk], log)
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", text, re.M)
    label = re.search(r"^application-label:'(.*)'$", text, re.M)
    if not package or not label:
        raise ValueError('无法读取 APK 的包名、版本或应用名称')
    return dict(package=package[1], versionCode=int(package[2]), versionName=package[3], label=label[1])


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('不安全的资源路径：' + name)
    return name


def matching_brace(text, opening):
    depth = 0
    quote = None
    comment = None
    i = opening
    while i < len(text):
        char = text[i]
        pair = text[i:i + 2]
        if comment == 'line':
            if char == '\n':
                comment = None
        elif comment == 'block':
            if pair == '*/':
                comment = None
                i += 1
        elif quote:
            if char == '\\':
                i += 1
            elif char == quote:
                quote = None
        elif pair == '//':
            comment = 'line'
            i += 1
        elif pair == '/*':
            comment = 'block'
            i += 1
        elif char in "\"'`":
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError('无法定位启动函数结束位置，当前原版启动结构需要适配')


def patch_bootstrap(source):
    source = source.replace('\r\n', '\n')
    original_on_start = re.search(r'var\s+onStart\s*=\s*function\s*\(\s*\)\s*\{', source)
    if not original_on_start:
        raise ValueError('未找到原版 onStart，停止以避免改变渲染启动流程')
    start_brace = original_on_start.end() - 1
    original_body = source[start_brace:matching_brace(source, start_brace) + 1]
    boot = re.search(r'window\.boot\s*=\s*function\s*\(\s*\)\s*\{', source)
    if not boot or 'HotUpdateSearchPaths' not in source[:boot.start()]:
        raise ValueError('未识别出支持的 Cocos 原生 main.js 启动结构；已停止构建')
    source = ('if (window.jsb) {\n'
              '    require("deepone-mod/md5.js");\n'
              '    require("deepone-mod/updater.js");\n'
              '    window.DeepOneMod.prepare();\n'
              '}\n\n' + source[boot.start():])
    # Resolve/download the overlay before window.boot loads project.js. Preserve the original onStart/render path.
    source, boot_count = re.subn(r'\bwindow\.boot\(\s*\)\s*;',
        'window.DeepOneMod.synchronize(function () { try { window.boot(); } catch (error) { window.DeepOneMod.bootstrapFailed(error); } });', source)
    if boot_count != 1:
        raise ValueError('原生 window.boot 启动调用数量不符合预期')
    scene = re.search(r'cc\.director\.loadScene\(\s*launchScene\s*,\s*function\s*\(\s*err\s*\)\s*\{', source)
    if not scene:
        raise ValueError('未找到启动场景加载回调')
    success = re.search(r'if\s*\(\s*!err\s*\)\s*\{', source[scene.end():])
    if not success:
        raise ValueError('未找到场景启动成功标记位置')
    pos = scene.end() + success.end()
    source = source[:pos] + '\n        window.DeepOneMod.markBootHealthy();\n' + source[pos:]
    source, count = re.subn(r'cc\.game\.run\(\s*option\s*,\s*onStart\s*\);',
                           'try { cc.game.run(option, onStart); } catch (error) { window.DeepOneMod.bootstrapFailed(error); }', source)
    if count != 1:
        raise ValueError('Cocos 游戏启动调用数量不符合预期')
    final_on_start = re.search(r'var\s+onStart\s*=\s*function\s*\(\s*\)\s*\{', source)
    start_brace = final_on_start.end() - 1
    final_body = source[start_brace:matching_brace(source, start_brace) + 1]
    final_body = final_body.replace('\n        window.DeepOneMod.markBootHealthy();\n', '')
    if final_body != original_body:
        raise ValueError('原版 onStart 渲染启动逻辑发生意外变化，停止构建')
    return source.encode('utf-8')


def seed_files(config):
    record_path = ROOT / 'vendor/upstream/current.json'
    record = read_json(record_path)
    commit = record['commit']
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('GitHub 快照提交无效')
    folder = ROOT / 'vendor/upstream' / commit
    for name, info in record['files'].items():
        path = folder / safe_member(name)
        if not path.is_file() or path.stat().st_size != info['bytes'] or sha256(path) != info['sha256']:
            raise ValueError('GitHub 快照文件损坏或被修改：' + name)
    manifest = read_json(folder / 'assets/project.manifest')
    script = (folder / 'assets/src/project.js').read_bytes()
    info = manifest['assets']['src/project.js']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', manifest['version']):
        raise ValueError('补丁资源版本格式不受支持')
    if info['size'] != len(script) or info['md5'] != hashlib.md5(script).hexdigest():
        raise ValueError('GitHub 补丁与清单不一致；请更新快照')
    if b'ntr.best' in script or b'window.__require' not in script:
        raise ValueError('GitHub 补丁不是预期的原生脚本')
    return script, dict(version=manifest['version'], source='bundled-github', size=info['size'], md5=info['md5']), record


def make_payload(archive, config, diagnostics=False):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError('原版 APK 含重复 ZIP 路径，不能安全构建')
    if any(name.startswith('assets/deepone-mod/') for name in names):
        raise ValueError('输入已经包含此 Mod，请使用原版 APK')
    candidates = []
    for name in names:
        if name.startswith('assets/') and name.endswith('.manifest'):
            try:
                data = json.loads(archive.read(name))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(data, dict) and isinstance(data.get('assets'), dict) and 'src/project.js' in data['assets']:
                candidates.append((safe_member(name), data))
    if len(candidates) != 1:
        raise ValueError(f'原生资源清单不唯一或不存在（找到 {len(candidates)} 个），需适配当前 APK')
    manifest_path, manifest = candidates[0]
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', str(manifest.get('version', ''))):
        raise ValueError('原版资源版本格式无效')
    for key in ['packageUrl', 'remoteManifestUrl', 'remoteVersionUrl']:
        value = manifest.get(key)
        if not isinstance(value, str) or not value.startswith('https://') or 'ntr.best' in value:
            raise ValueError('输入 APK 不是预期的官方资源更新源：' + key)
    fonts = [safe_member(name) for name in names if name.startswith('assets/res/') and name.endswith(config['font_member_suffix'])]
    if len(fonts) != 1:
        raise ValueError('未能唯一定位 Humming-D.ttf 字体资源')
    font_relative = fonts[0].removeprefix('assets/')
    font_path = ROOT / 'vendor/Humming-D.ttf'
    if sha256(font_path) != config['font_sha256']:
        raise ValueError('中文字体素材摘要不匹配')
    source_script = archive.read('assets/src/project.js')
    if b'ntr.best' in source_script or b'window.__require' not in source_script:
        raise ValueError('输入的游戏脚本不是预期的原版脚本')
    seed, seed_info, pinned = seed_files(config)
    runtime = dict(release=config['release'], diagnostics=diagnostics, officialPackageUrl=manifest['packageUrl'],
                   officialManifestUrl=manifest['remoteManifestUrl'], officialVersionUrl=manifest['remoteVersionUrl'],
                   githubPagesAssets=config['github_pages_assets'],
                   githubRawAssets=f'https://raw.githubusercontent.com/{config["github_repository"]}/{config["github_branch"]}/assets/',
                   manifestAsset=manifest_path.removeprefix('assets/'), fontRelative=font_relative)
    payload = {
        'assets/main.js': patch_bootstrap(archive.read('assets/main.js').decode('utf-8')),
        fonts[0]: font_path.read_bytes(),
        'assets/deepone-mod/font/' + font_relative: font_path.read_bytes(),
        'assets/deepone-mod/runtime-config.json': json.dumps(runtime, ensure_ascii=False, indent=2).encode('utf-8'),
        'assets/deepone-mod/patch-seed/src/project.js': seed,
        'assets/deepone-mod/patch-seed/manifest.json': json.dumps(seed_info, indent=2).encode('utf-8'),
    }
    # Expose the APK's own CryptoJS to the pre-boot updater, as in vc32.
    crypto = archive.read('assets/src/assets/script/lib/crypto/crypto-js.js').replace(b'\r\n', b'\n')
    wrapper = (b'"object" == typeof exports ? module.exports = exports = e() : '
               b'"function" == typeof define && define.amd ? define([], e) : t.CryptoJS = e();')
    if crypto.count(wrapper) != 1:
        raise ValueError('原版 CryptoJS 导出结构发生变化，需要适配此 APK')
    payload['assets/deepone-mod/md5.js'] = crypto.replace(wrapper, b't.DeepOneModCrypto = e();')
    for name in ['updater.js', 'compatibility.js', 'legacy-repair.json']:
        payload['assets/deepone-mod/' + name] = (ROOT / 'runtime' / name).read_bytes()
    updater_source = payload['assets/deepone-mod/updater.js'].decode('utf-8')
    if re.search(r'new\s+cc\.(?:Scene|Node)\b|\bcc\.(?:Canvas|Graphics|Camera)\b|cc\.game\.restart\s*\(', updater_source):
        raise ValueError('汉化启动器包含额外画布/场景或自动重启，停止构建')
    engine = archive.read('assets/src/cocos2d-jsb.js').decode('utf-8')
    engine_version = re.search(r'cc\.ENGINE_VERSION\s*=\s*["\']([^"\']+)', engine)
    return payload, dict(resourceVersion=manifest['version'], manifestPath=manifest_path, fontPath=fonts[0],
                         engineVersion=engine_version[1] if engine_version else 'unknown', seed=seed_info, github=pinned)


def build(args):
    config = read_json(ROOT / 'config.json')
    source_path = Path(args.apk).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError('原版 APK 不存在：' + str(source_path))
    for asset in ['Humming-D.ttf', 'signing/testkey.pk8', 'signing/testkey.x509.pem', 'upstream/current.json']:
        if not (ROOT / 'vendor' / asset).is_file():
            raise ValueError('缺少构建素材 ' + asset + '，请先运行 python prepare_assets.py')
    for key in ['source_package', 'target_package']:
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', config[key]):
            raise ValueError('配置中的包名无效：' + key)
    tools = find_build_tools(args.build_tools)
    java = args.java or shutil.which('java')
    node = args.node or shutil.which('node')
    if not java:
        raise ValueError('未找到 Java，请配置 PATH 或使用 --java')
    if not node:
        raise ValueError('未找到 Node.js（仅用于脚本语法检查），请配置 PATH 或使用 --node')
    if args.refresh:
        from refresh_upstream import refresh
        print('正在获取并固定 GitHub 补丁快照…', flush=True)
        refresh(config)
    source_info = metadata(executable(tools / 'aapt'), source_path)
    if source_info['package'] != config['source_package']:
        raise ValueError(f'需要原版包 {config["source_package"]}，实际为 {source_info["package"]}')
    state_path = ROOT / '.build-state.json'
    previous_code = read_json(state_path).get('lastVersionCode', 0) if state_path.exists() else 0
    code = args.version_code if args.version_code is not None else max(int(config['version_code_floor']), source_info['versionCode'] + 1, previous_code + 1)
    if not source_info['versionCode'] < code <= 2100000000:
        raise ValueError('versionCode 必须高于输入原版，且不超过 Android 上限')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    work = ROOT / 'build' / f'vc{code}-{stamp}'
    work.mkdir(parents=True, exist_ok=False)
    output = Path(args.output).expanduser().resolve() if args.output else ROOT / 'output' / f'DeepOneR-Mod-{config["release"]}-vc{code}.apk'
    if output == source_path:
        raise ValueError('输出不能覆盖输入 APK')
    if output.exists() and not args.overwrite:
        raise ValueError('输出文件已存在，请换一个路径或显式使用 --overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(source_path)
    signing = ROOT / 'vendor/signing'
    cert = x509.load_pem_x509_certificate((signing / 'testkey.x509.pem').read_bytes())
    key = serialization.load_der_private_key((signing / 'testkey.pk8').read_bytes(), password=None)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    if fingerprint != config['signer_sha256'] or key.public_key().public_numbers() != cert.public_key().public_numbers():
        raise ValueError('工程签名身份不匹配旧版 Mod')
    print(f'输入：{source_path}\n输出：{output}\nAndroid versionCode：{code}', flush=True)
    with ZipFile(source_path) as source:
        payload, details = make_payload(source, config, diagnostics=args.debuggable)
        payload['AndroidManifest.xml'] = patch_manifest(source.read('AndroidManifest.xml'), source_info['package'], config['target_package'], code)
        if args.debuggable:
            payload['AndroidManifest.xml'] = enable_debuggable(payload['AndroidManifest.xml'])
        payload['resources.arsc'], resource_changes = patch_resources(source.read('resources.arsc'), source_info['package'],
            config['target_package'], source_info['label'], config['application_label'])
        payload_dir = work / 'payload'
        for name, content in payload.items():
            target = payload_dir / safe_member(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for name in ['assets/main.js', 'assets/deepone-mod/updater.js', 'assets/deepone-mod/compatibility.js', 'assets/deepone-mod/md5.js', 'assets/deepone-mod/patch-seed/src/project.js']:
            run([node, '--check', payload_dir / name])
        unsigned = work / 'unsigned.apk'
        aligned = work / 'aligned.apk'
        candidate = work / 'signed.apk'
        replacements = dict(payload)
        print('正在写入 Mod 内容并重新打包…', flush=True)
        with ZipFile(unsigned, 'w') as target:
            for info in source.infolist():
                name = info.filename
                if name.startswith('META-INF/') and (name.endswith(('.RSA', '.DSA', '.EC', '.SF')) or name == 'META-INF/MANIFEST.MF'):
                    continue
                content = replacements.pop(name, None)
                if content is None:
                    content = source.read(info)
                clean = copy.copy(info)
                clean.extra = b''
                target.writestr(clean, content)
            for name, content in replacements.items():
                target.writestr(name, content, compress_type=ZIP_DEFLATED, compresslevel=6)
    print('正在对齐、签名和检查安装包结构…', flush=True)
    run([executable(tools / 'zipalign'), '-P', '16', '-f', '4', unsigned, aligned], work / 'alignment.log')
    run([java, '-jar', tools / 'lib/apksigner.jar', 'sign', '--key', signing / 'testkey.pk8', '--cert', signing / 'testkey.x509.pem',
         '--v1-signing-enabled', 'true', '--v2-signing-enabled', 'true', '--v3-signing-enabled', 'true', '--out', candidate, aligned], work / 'signing.log')
    verification = run([java, '-jar', tools / 'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', candidate], work / 'signature-verification.txt')
    if f'certificate SHA-256 digest: {fingerprint}' not in verification:
        raise ValueError('输出 APK 签名身份不匹配')
    run([executable(tools / 'zipalign'), '-c', '-P', '16', '4', candidate], work / 'alignment-verification.txt')
    result_info = metadata(executable(tools / 'aapt'), candidate, work / 'apk-info.txt')
    if result_info != dict(package=config['target_package'], versionCode=code, versionName=source_info['versionName'], label=config['application_label']):
        raise ValueError('输出 APK 包名/版本/应用名称不符合预期')
    with ZipFile(candidate) as result, ZipFile(source_path) as original:
        for name, content in payload.items():
            if result.read(name) != content:
                raise ValueError('输出 APK 文件内容不一致：' + name)
        for name in original.namelist():
            if name == 'classes.dex' or name.startswith('lib/') or name == 'assets/src/project.js':
                if result.read(name) != original.read(name):
                    raise ValueError('原版程序文件意外改变：' + name)
    if sha256(source_path) != source_hash:
        raise ValueError('构建过程中输入 APK 发生变化，停止发布输出')
    # Publish only after structural/signature checks. The input is never moved or replaced.
    shutil.copyfile(candidate, output)
    record = dict(release=config['release'], apk=output.name, size=output.stat().st_size, sha256=sha256(output),
                  input=dict(sha256=source_hash, **source_info), output=result_info,
                  signerSha256=fingerprint, upgradeCompatibleWithLegacyMod=True, debuggable=args.debuggable, resourceEdits=resource_changes,
                  details=details, validation='Build/syntax/signature/alignment only. No application tests, installation, or device access.')
    report = output.with_suffix('.build.json')
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.sha256').write_text(record['sha256'] + '  ' + output.name + '\n', encoding='utf-8')
    new_state = state_path.with_suffix('.new')
    new_state.write_text(json.dumps(dict(lastVersionCode=code, lastOutput=output.name), indent=2), encoding='utf-8')
    new_state.replace(state_path)
    if not args.keep_intermediates:
        # Only explicitly created intermediate files, not directories or user resources.
        for path in [unsigned, aligned, candidate, candidate.with_suffix('.apk.idsig')]:
            if path.is_file():
                path.unlink()
    print(f'完成：{output}\nSHA-256：{record["sha256"]}\n构建信息：{report}', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', help='原版 APK 路径；支持中文、空格和任意文件名')
    parser.add_argument('--output', '-o', help='输出 APK 的完整路径；默认工程 output/ 目录')
    parser.add_argument('--version-code', type=int, help='显式指定 Android versionCode，必须高于输入原版')
    parser.add_argument('--refresh', action='store_true', help='构建前更新 GitHub 补丁快照；默认使用本地已准备的快照')
    parser.add_argument('--debuggable', action='store_true', help='仅排障：临时启用 run-as 和不含账号内容的运行数据诊断；不要作为正式包发布')
    parser.add_argument('--build-tools', help='Android SDK build-tools 目录；默认自动发现')
    parser.add_argument('--java', help='Java 可执行文件路径')
    parser.add_argument('--node', help='Node.js 可执行文件路径，仅做 JS 语法检查')
    parser.add_argument('--overwrite', action='store_true', help='允许覆盖指定的已有输出 APK，始终不覆盖输入')
    parser.add_argument('--keep-intermediates', action='store_true', help='保留未签名、对齐和签名后的中间 APK')
    try:
        build(parser.parse_args())
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print('构建失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
