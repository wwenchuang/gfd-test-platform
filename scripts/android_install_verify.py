"""Install a pinned standalone APK on an explicitly selected Android test device."""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def checked(command, timeout=180):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or '命令执行失败').strip()[-500:])
    return result.stdout.strip()


def install(args):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+', args.package):
        raise ValueError('应用包名无效')
    if not re.fullmatch(r'[a-zA-Z0-9._:-]+', args.device):
        raise ValueError('设备编号无效')
    if not re.fullmatch('[0-9a-f]{64}', args.sha256):
        raise ValueError('需要已核实的 APK SHA-256')
    adb = [args.adb, '-s', args.device]
    def shell(*command):
        return checked([*adb, 'shell', *command])
    if checked([*adb, 'get-state']) != 'device':
        raise ValueError('指定设备未连接')
    installed = 'package:' + args.package in shell('pm', 'list', 'packages', '--user', '0', args.package).splitlines()
    if args.mode == 'new' and installed:
        raise ValueError('新安装需要目标机未安装此应用')
    if args.mode != 'new' and not installed:
        raise ValueError('目标机尚未安装此应用')
    if args.mode != 'verify':
        if not args.apk or not args.apk.is_file():
            raise ValueError('需要提供已下载的 APK')
        with args.apk.open('rb') as file:
            digest = hashlib.sha256()
            for block in iter(lambda: file.read(1024*1024), b''):
                digest.update(block)
        if digest.hexdigest() != args.sha256:
            raise ValueError('下载文件与构建指纹不一致')
        if args.mode == 'reinstall':
            if not args.confirm_clear_data:
                raise ValueError('重新安装会清除数据，需明确选择清除本机应用数据')
            # Verify the actual package identity before removing anything.
            metadata = checked([args.aapt, 'dump', 'badging', str(args.apk)])
            attrs = dict(re.findall(r"(\w+)='([^']*)'", metadata.splitlines()[0]))
            if (attrs.get('name'), attrs.get('versionName'), attrs.get('versionCode')) != (
                    args.package, args.version, args.code):
                raise ValueError('APK 清单与目标应用或构建不一致')
            checked([os.environ.get('APKSIGNER_BIN', 'apksigner'), 'verify', str(args.apk)])
            if shell('pm', 'uninstall', '--user', '0', args.package) != 'Success':
                raise ValueError('当前 Android 用户的应用未成功卸载')
        options = ['-r'] if args.mode == 'upgrade' else []
        receipt = checked([*adb, 'install', *options, '--user', '0', '--pkg', args.package, str(args.apk)])
        if 'Success' not in receipt.splitlines():
            raise ValueError('未取得安装成功回执')
    paths = shell('pm', 'path', '--user', '0', args.package).splitlines()
    if len(paths) != 1 or not re.fullmatch(r'package:/[A-Za-z0-9_./=~+-]+/base\.apk', paths[0]):
        raise ValueError('未找到可核验的单 APK 安装')
    actual = shell('sha256sum', paths[0][8:]).split()[0]
    info = shell('dumpsys', 'package', args.package)
    versions = re.findall(r'^\s*versionName=([^\s]+)', info, re.M)
    codes = re.findall(r'^\s*versionCode=(\d+)\b', info, re.M)
    if actual != args.sha256 or versions[:1] != [args.version] or codes[:1] != [args.code]:
        raise ValueError('安装后读取的版本或文件指纹与预期不一致')
    return dict(device=args.device, package=args.package, version=args.version,
                version_code=args.code, sha256=actual, mode=args.mode, verified=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('device', 'package', 'version', 'code', 'sha256'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--mode', choices=('verify', 'new', 'upgrade', 'reinstall'), default='verify')
    parser.add_argument('--apk', type=Path)
    parser.add_argument('--adb', default=os.environ.get('ADB_BIN', 'adb'))
    parser.add_argument('--aapt', default=os.environ.get('AAPT_BIN', 'aapt'))
    parser.add_argument('--confirm-clear-data', action='store_true')
    try:
        print(json.dumps(install(parser.parse_args()), ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')
