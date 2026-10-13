"""Concise release-application reports derived only from a frozen batch snapshot."""
import re
from datetime import datetime, timedelta, timezone

ZONE = timezone(timedelta(hours=8))
RESULTS = {'passed': '通过', 'success': '通过', 'failed': '未通过', 'timeout': '超时',
           'cancelled': '已取消', 'interrupted': '执行中断', 'blocked': '未执行',
           'queued': '未执行', 'pending': '未执行', 'skipped': '未执行',
           'running': '未完成', 'cancelling': '正在停止'}


def safe_text(value):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    return value.translate(str.maketrans({'<': '＜', '>': '＞', '|': '｜', '[': '［', ']': '］',
        '`': '｀', '*': '＊', '_': '＿', '#': '＃', '!': '！', '\\': '＼'}))


def date(value):
    return datetime.fromtimestamp(value / 1e9, ZONE).strftime('%Y年%m月%d日 %H:%M:%S') if value else '未记录'


def _name(value, fallback):
    value = str(value or '').strip()
    return fallback if not value or re.fullmatch(r'[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+){2,}', value) else value


def build_presentation(snapshot):
    attempts = snapshot.get('attempts') or []
    app = _name(snapshot.get('app_name'), '应用').replace('测试报告', '').strip() or '应用'
    title = app + (' ' if app != '应用' else '') + '发布测试报告'
    cases = {a['case_id'] for a in attempts}
    executed = {a['case_id'] for a in attempts if a.get('started_ns')}
    passed = sum(a.get('status') in ('passed', 'success') for a in attempts)
    failed = sum(a.get('status') in ('failed', 'timeout') for a in attempts)
    incomplete = len(attempts) - passed - failed
    if failed:
        outcome, conclusion = 'failed', '本次测试未通过。请处理未通过项并复测后，再确认发布条件。'
    elif attempts and not incomplete and len(executed) == len(cases):
        outcome, conclusion = 'passed', '本次测试通过，所测范围满足本次发布验证要求。'
    else:
        outcome, conclusion = 'incomplete', '本次测试未完成，尚不能形成通过结论。请补齐未完成项。'
    owner = str(snapshot.get('owner_display_name') or '未记录')
    coverage, environments, concerns, test_versions = [], [], [], []
    targets_by_platform, target_order = {}, {}
    def target_key(target):
        return tuple(str(target.get(key) or '') for key in ('platform', 'runner_id', 'device_id'))
    # Prefer the frozen selection order; legacy snapshots fall back to first appearance.
    for target in (snapshot.get('selection') or {}).get('targets') or []:
        target_order.setdefault(target_key(target), len(target_order) + 1)
    for index, attempt in enumerate(attempts, 1):
        meta, device = attempt.get('metadata') or {}, attempt.get('device_snapshot') or {}
        name = _name(meta.get('task_name'), f'用例 {index}')
        scenario = str(meta.get('goal') or name + '（未填写独立场景说明）')
        expected = str(meta.get('expected') or '；'.join(attempt.get('expectations') or []) or '未填写')
        result = RESULTS.get(attempt.get('status'), '状态待确认') if attempt.get('started_ns') else '未执行'
        attempt_no = int(attempt.get('attempt_no') or 1)
        platform = {'ios': '苹果系统', 'android': '安卓系统'}.get(attempt.get('platform'), '系统未记录')
        phone = str(device.get('label') or device.get('model') or '测试手机')
        phone = re.sub(r'(?i)apple\s*', '', phone)
        phone = re.sub(r'(?i)iphone\s*', '苹果 ', phone)
        phone = phone.replace('Pro Max', '专业大屏版').replace('Pro', '专业版').replace('Plus', '大屏版')
        version = device.get('ios_version') or device.get('android_version') or device.get('os_version') or '版本未记录'
        app_versions = []
        # The execution contract freezes only the selected app's installed version.
        for item in device.get('app_versions') or []:
            version_text = str(item.get('version_name') or '未获取')
            build = item.get('version_code')
            if build is not None and str(build).strip():
                version_text += f'（构建号 {build}）'
            app_versions.append(version_text)
        apps = '、'.join(app_versions) or '未获取'
        app_platform = {'ios': '苹果端', 'android': '安卓端'}.get(attempt.get('platform'), '平台未记录')
        tested_version = f'{app_platform} {apps}'
        if tested_version not in test_versions:
            test_versions.append(tested_version)
        environment = f'{phone}，{platform} {version}；应用版本 {apps}'
        key = target_key(attempt)
        target_order.setdefault(key, len(target_order) + 1)
        targets_by_platform.setdefault(platform, set()).add(key)
        coverage.append({'case_name': name, 'scenario': scenario, 'expected': expected, 'result': result,
                         'attempt_no': attempt_no, 'platform': attempt.get('platform'),
                         'platform_label': platform, 'target_label': platform,
                         'environment': environment})
    for attempt, row in zip(attempts, coverage):
        # Bind compact row labels to full frozen environments in the overview.
        environment = row['environment']
        if len(targets_by_platform[row['platform_label']]) > 1:
            ordinal = target_order[target_key(attempt)]
            row['target_label'] += f'，设备 {ordinal}'
            environment = f'设备 {ordinal}：{environment}'
        if environment not in environments:
            environments.append(environment)
        if row['result'] != '通过':
            reason = str(attempt.get('block_message') or '').strip()
            # Raw diagnostics remain in the private snapshot/evidence, not in the release brief.
            if not re.search(r'[\u4e00-\u9fff]', reason):
                reason = '未开始执行，本场景尚未验证。' if not attempt.get('started_ns') else '请在平台查看本次执行证据，确认原因后复测。'
            concerns.append(f"{row['case_name']}（{row['target_label']}）：{row['result']}。{reason}")
    summary = f'{title}：测试版本 {"；".join(test_versions) or "未获取"}。{owner}执行 {len(cases)} 条用例，共 {len(attempts)} 次验证；通过 {passed} 项，未通过 {failed} 项，未完成 {incomplete} 项。{conclusion}'
    return {'schema_version': 1, 'title': title, 'owner': owner, 'time_range': f"{date(snapshot.get('started_ns'))} 至 {date(snapshot.get('finished_ns'))}（北京时间）",
            'test_versions': test_versions,
            'conclusion_icon': {'passed':'✅', 'failed':'❌', 'incomplete':'⏳'}[outcome],
            'conclusion_status': outcome, 'conclusion': conclusion, 'summary': summary,
            'case_count': len(cases), 'executed_cases': len(executed), 'attempt_count': len(attempts),
            'passed_count': passed, 'failed_count': failed, 'incomplete_count': incomplete,
            'coverage': coverage, 'environments': environments, 'concerns': concerns,
            'is_rerun': bool((snapshot.get('selection') or {}).get('rerun_of'))}


def render_markdown(view):
    text = safe_text
    lines = [f"# {text(view['title'])}", '', '## '+view['conclusion_icon']+' 测试结论', text(view['conclusion']),
             f"本次共 {view['case_count']} 条用例，已执行 {view['executed_cases']} 条；通过 {view['passed_count']} 项，未通过 {view['failed_count']} 项，未完成 {view['incomplete_count']} 项。",
             '', '## 📋 测试概况', f"测试版本：{text('；'.join(view['test_versions']) or '未获取')}",
             f"执行人：{text(view['owner'])}", f"测试时间：{text(view['time_range'])}"]
    for environment in view['environments']:
        lines.append('测试环境：' + text(environment))
    if view['is_rerun']:
        lines.append('本报告为复测结果；原测试结果保留在平台中。')
    lines += ['', '## 🎯 覆盖场景及结果', '| 测试用例 | 覆盖场景与预期 | 结果 |', '| --- | --- | --- |']
    for item in view['coverage']:
        target = item.get('target_label') or {'ios': '苹果系统', 'android': '安卓系统'}.get(item.get('platform'), '系统未记录')
        name = item['case_name'] + f'（{target}）' + (f"（第 {item['attempt_no']} 次）" if item['attempt_no'] > 1 else '')
        scope = item['scenario'] + '；预期：' + item['expected']
        lines.append('| ' + ' | '.join(map(text, [name, scope, item['result']])) + ' |')
    if view['concerns']:
        lines += ['', '## ⚠️ 需关注事项'] + ['- ' + text(item) for item in view['concerns']]
    lines += ['', '本结论仅针对上述应用版本与测试范围。相关执行证据保留在平台，供复核。']
    return '\n'.join(lines)
