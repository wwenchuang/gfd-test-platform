"""Find a scoped mobile build in a Feishu group and install it on the configured Runner."""
import argparse
import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from android_install_verify import install

FEISHU = 'https://open.feishu.cn/open-apis'
TOOLS = Path(__file__).resolve().parent


def api(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'} if data is not None else {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(FEISHU + path, json.dumps(data).encode() if data is not None else None, headers)
    with urlopen(request, timeout=20) as response:
        result = json.load(response)
    if result.get('code') != 0:
        raise ValueError('飞书读取失败，业务码 ' + str(result.get('code')))
    return result


def strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)
    elif isinstance(node, str):
        yield node


def notice(message, profile):
    if message.get('deleted') or message.get('sender', {}).get('id') != profile['sender_id']:
        return None
    body = '\n'.join(strings(json.loads(message['body']['content'])))
    if profile['title'] not in body:
        return None
    def field(pattern):
        values = set(re.findall(pattern, body, re.I))
        return values.pop() if len(values) == 1 else None
    version = field(r'APP版本\s*[:：]\s*([\d.]+)\b')
    code = field(r'版本号\s*[:：]\s*(\d+)\b')
    build = field(r'构建编号\s*[:：]\s*(\d+)\b')
    explicit = field(r'(?:打包环境|发布环境|环境)\s*[:：]\s*(QA|测试环境|正式环境|生产环境)(?=\s|$)')
    release_note = bool(re.search(r'更新内容\s*[:：]\s*[·“"\s]*上架包[”"\s]*(?:$|\n)', body))
    environment = {'qa': 'qa', '测试环境': 'qa', '正式环境': 'production', '生产环境': 'production'}.get((explicit or '').lower())
    if release_note and environment == 'qa':
        raise ValueError('同一通知同时标为 QA 和上架包')
    if not environment and release_note:
        environment = 'production'
    if version != profile['version'] or environment != profile['environment']:
        return None
    links = set(re.findall(r'https://(?:www\.)?pgyer\.com/(?:app/build/)?[A-Za-z0-9_-]+(?=$|[\s"<>）)])', body))
    if not build or not code or len(links) != 1:
        raise ValueError('匹配的打包通知缺少唯一的构建号、版本号或下载页')
    return dict(build=build, version=version, code=code, url=links.pop(), message_id=message['message_id'])


def latest(profile):
    token = api('/auth/v3/tenant_access_token/internal', {
        'app_id': os.environ['FEISHU_BUILD_APP_ID'],
        'app_secret': os.environ['FEISHU_BUILD_APP_SECRET']})['tenant_access_token']
    params = dict(container_id_type='chat', container_id=profile['chat_id'],
                  sort_type='ByCreateTimeDesc', page_size=50)
    seen = set()
    while True:
        page = api('/im/v1/messages?' + urlencode(params), token=token)['data']
        for message in page['items']:
            candidate = notice(message, profile)
            if candidate:
                return candidate
        cursor = page.get('page_token')
        if not page.get('has_more'):
            raise ValueError('指定打包群没有匹配的应用版本及环境')
        if not cursor or cursor in seen:
            raise ValueError('飞书分页异常')
        seen.add(cursor)
        params['page_token'] = cursor


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('profile', type=Path, help='Runner 管理员保存的环境配置 JSON')
    cli.add_argument('--mode', choices=['lookup', 'new', 'upgrade', 'reinstall'], default='lookup')
    cli.add_argument('--confirm-clear-data', action='store_true')
    cli.add_argument('--page', help='手动选定正式环境构建的蒲公英网页')
    cli.add_argument('--build', help='飞书通知里的蒲公英 Build')
    cli.add_argument('--code', help='Android 应用版本号')
    args = cli.parse_args()
    cfg = json.loads(args.profile.read_text())
    if cfg.get('runner_env_file'):
        for line in Path(cfg['runner_env_file']).read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip(chr(34)).strip(chr(39)))
    if cfg.get('node_modules'):
        os.environ['NODE_PATH'] = cfg['node_modules']
    if cfg['environment'] not in ('qa', 'production') or not all(
            cfg.get(k) for k in ('package', 'device', 'version')):
        raise ValueError('配置缺少设备、环境或应用')
    if args.page:
        if cfg['environment'] != 'production' or not (args.code or '').isdigit() or not (args.build or '').isdigit():
            raise ValueError('手动指定正式版网页需同时给出 Build 和 Android 版本号')
        build = dict(url=args.page, version=cfg['version'], code=args.code, build=args.build, message_id=None)
    else:
        if not all(cfg.get(k) for k in ('chat_id', 'sender_id', 'title')):
            raise ValueError('尚未绑定打包群或通知机器人')
        build = latest(cfg)
    if args.mode == 'lookup':
        print(json.dumps(build, ensure_ascii=False))
        return
    apply_build(cfg, build, args)


def apply_build(cfg, build, args):
    if not Path(cfg['smoke_yaml']).is_file() or not Path(cfg['runner_script']).is_file():
        raise ValueError('此环境尚未配置可用的冒烟脚本或 Runner')
    with tempfile.TemporaryDirectory() as tempdir:
        args.apk = Path(tempdir) / 'install.apk'
        raw = subprocess.check_output(['node', str(TOOLS / 'pgy_web_download.cjs'), '--url', build['url'],
            '--package', cfg['package'], '--version', build['version'], '--build', build['build'],
            '--output', str(args.apk), '--environment', cfg['environment']], text=True, timeout=300)
        args.sha256 = json.loads(raw)['sha256']
        args.device, args.package = cfg['device'], cfg['package']
        args.code, args.version = build['code'], build['version']
        args.adb, args.aapt = os.getenv('ADB_BIN', 'adb'), os.getenv('AAPT_BIN', 'aapt')
        receipt = {**install(args), 'build': build['build'], 'message_id': build['message_id']}
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    run_smoke(cfg)


def run_smoke(cfg):
    import yaml
    case = Path(cfg['smoke_yaml']).resolve()
    content = yaml.safe_load(case.read_text())
    if content.get('android', {}).get('deviceId') != cfg['device']:
        raise ValueError('冒烟 YAML 的指定设备与安装目标不一致')
    launches = {step['launch'] for task in content.get('tasks', []) for step in task.get('flow', []) if 'launch' in step}
    if launches != {cfg['package']}:
        raise ValueError('冒烟 YAML 的启动目标与安装包不一致')
    spec = importlib.util.spec_from_file_location('android_runner', cfg['runner_script'])
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    out = Path(cfg['reports_dir']).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    report_dir = Path(tempfile.mkdtemp(prefix='安装后冒烟-', dir=out))
    args = runner.execution_command(runner.resolve_command(runner.MIDSCENE_BIN,'Midscene'), case, cfg['device'])
    env = runner.progress_observer_env(report_dir, cfg['device'], False, cfg['package'])
    runner.configure_zxb_model_transport(env, True)
    with (report_dir/'执行.log').open('w') as log:
        result = subprocess.run(args, cwd=report_dir, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    reports = list((report_dir/'midscene_run/report').glob('*.html'))
    summaries = list((report_dir/'midscene_run/output').glob('summary*.json'))
    summary = json.loads(summaries[0].read_text()).get('summary', {}) if len(summaries) == 1 else {}
    ok = (result.returncode == 0 and len(reports) == 1 and summary.get('total', 0) > 0
          and summary.get('successful') == summary['total'])
    print(json.dumps({'smoke_passed': ok, 'report': str(reports[0] if reports else report_dir)}, ensure_ascii=False))
    if not ok:
        raise ValueError('安装后冒烟未通过，见本次执行报告')


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print('获取或安装构建失败：' + str(error), file=sys.stderr)
        sys.exit(1)
