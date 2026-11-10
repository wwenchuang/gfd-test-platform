"""Run and resume the private PRD/prototype prompt comparison. See docs/audits/week7-design-evaluation.md."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
import unicodedata

from task_server.services import requirement_design_model as design


GROUPS = ('g1', 'g2', 'g3', 'g4')
ARMS = ('zero', 'few')
VERSION = 'requirement-evaluation.zh.v1'
BASE = design.SYSTEM_PROMPT + """
本次评估按以下顺序分析。先从当前输入逐条抽取明确事实，其次仅从实际提供的图片识别可见控件、状态和提示。
在 JSON 中增加 observations 数组。每行包含 kind（fact、visual 或 unknown）、text（具体观察或待确认原因）、refs（当前输入中适用的来源编号数组）。
分别核对正向、边界、权限、状态变化、异常恢复和可访问性风险。没有依据的行为放在 unknown 或 questions，说明需要什么证据；
未经证实的推测不得写成用例预期。单张图不能证明键盘操作、读屏效果、错误分支或保存成功。
然后设计测试点和用例，每个用例只检验一个主要行为，前置和期望要能复现和观察。当前输入若支持，给出 5 条不同用例；
若支持不足，减少数量，说明缺口。只使用当前材料的来源编号，绝不从示例复制业务内容或新造接口。
"""
EXAMPLES = """
以下示例独立于待分析需求，仅展示推导方式。在图书馆自助借阅材料中，若 P1 明确写“还书后从当前借阅列表移除”，
fact 可以写“归还成功后该条记录从当前列表移除”，测试步骤可以先借一本可归还的书再归还，预期仅断言该条记录消失。
若 V1 只显示灰色续借按钮，visual 可以写“续借按钮呈灰色”，unknown 可以写“禁用原因和读屏状态需要规则与运行时信息”。
以上不是当前输入的来源或结论。"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def read(path):
    return json.loads(path.read_bytes())


def write(path, data):
    path.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


def save(path, value):
    write(path, encode(value))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def system_prompt(arm):
    return BASE + (EXAMPLES if arm == 'few' else '')


def observations(draft, manifest):
    rows = []
    for row in design._array(draft, 'observations', 60):
        kind = row.get('kind') if isinstance(row, dict) else None
        refs = [] if kind == 'unknown' and row.get('refs') == [] else design._references(row, 'refs', manifest)
        if kind not in {'fact', 'visual', 'unknown'}:
            raise ValueError('invalid_observation')
        if kind == 'visual' and not any(manifest[r]['kind'] == 'prototype' for r in refs):
            raise ValueError('visual_without_prototype')
        rows.append(dict(kind=kind, text=design._string(row, 'text'), refs=refs))
    if not rows:
        raise ValueError('missing_observations')
    return rows


def load_groups(root):
    groups = {}
    for gid in GROUPS:
        path = root / gid
        src = read(path / 'source.json')
        content, manifest, fingerprint = design._input(src, path)
        clauses = [{k:item[k] for k in ('id','text')} for item in src['clauses']]
        images = [dict(id=item['id'], caption=item.get('caption',''), sha256=item['sha256']) for item in src['prototypes']]
        groups[gid] = dict(title=src['title'], content=content, source_manifest=manifest, input_sha256=fingerprint,
                           clauses=clauses, prototypes=images,
                           sample_count=len(src['clauses']), prototype_count=len(src['prototypes']))
    return groups


@contextmanager
def output_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError('output_must_be_private_0700')
    with (directory / '.run.lock').open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('evaluation_already_running') from None
        yield


def freeze(out, groups, model):
    parameters = design.generation_parameters(model)
    plan = dict(version=VERSION, requested_model=model, parameters=parameters,
                inputs={gid: g['input_sha256'] for gid, g in groups.items()},
                prompt_sha256={arm: digest(system_prompt(arm).encode()) for arm in ARMS})
    file = out / 'private/plan.json'
    if file.exists() and read(file) != plan:
        raise ValueError('frozen_inputs_model_or_prompt_changed')
    save(file, plan)
    for arm in ARMS:
        write(file.parent / (arm + '-prompt.txt'), system_prompt(arm).encode())
    return plan


def validated_record(group, arm, draft, receipt, request_id, started, finished, elapsed):
    result = design._validated_draft(draft, group['source_manifest'])
    obs = observations(draft, group['source_manifest'])
    evidence = dict(model=design._metadata_string(receipt.get('model')), request_id=design._metadata_string(request_id or receipt.get('request_id')),
                    response_id=design._metadata_string(receipt.get('id')), elapsed_ms=elapsed,
                    started_at=started, completed_at=finished, usage={k:v for k,v in (receipt.get('usage') or {}).items()
                    if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0})
    if not evidence['response_id'] or not evidence['model']:
        raise ValueError('missing_provider_receipt')
    report = dict(variant=arm, status='completed', **evidence, point_count=len(result['points']),
                  case_count=len(result['cases']), question_count=len(result['questions']),
                  referenced_source_ids=sorted({x for p in result['points'] for x in p['refs']}),
                  unreferenced_source_ids=result['checks']['unreferenced_source_ids'])
    record = dict(report=report, result=result, observations=obs)
    return record


def replay_arm(out, gid, group, arm):
    folder = out / 'private' / gid / arm
    # Compare the earliest response that passes the current rules in each arm.
    history = []
    for attempt in folder.glob('attempt-*'):
        for filename in ('validated.json','failure.json'):
            if (attempt / filename).is_file():
                history.append((read(attempt / filename)['report']['started_at'], attempt, filename))
                break
    last_failure = dict(report=dict(variant=arm, status='pending'))
    for started, attempt, filename in sorted(history):
        previous = read(attempt / filename)
        if filename == 'validated.json':
            return previous
        last_failure = previous
        if previous['report']['error'] != 'invalid_refs':
            continue
        meta = read(attempt / 'response-metadata.json')
        raw = (attempt / 'raw-response.json').read_bytes()
        if digest(raw) != meta['sha256']:
            raise ValueError('receipt_digest_mismatch')
        receipt = json.loads(raw)
        choice = receipt['choices'][0]
        if choice.get('finish_reason') != 'stop':
            continue
        try:
            record = validated_record(group, arm, json.loads(choice['message']['content']), receipt,
                                      meta['request_id'], started, previous['report']['completed_at'],
                                      previous['report']['elapsed_ms'])
        except ValueError:
            continue
        record['report']['revalidated'] = True
        save(attempt / 'revalidated.json',record)
        return record
    return last_failure


def run_arm(out, gid, group, arm, model):
    folder = out / 'private' / gid / arm
    saved = folder / 'accepted.json'
    if saved.exists():
        return read(saved)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(dir=folder, prefix='attempt-'))
    started = timestamp()
    clock = time.monotonic()
    def capture(raw, request_id):
        write(attempt / 'raw-response.json', raw)
        save(attempt / 'response-metadata.json', dict(request_id=design._metadata_string(request_id), sha256=digest(raw)))
    try:
        draft, receipt, request_id = design._call(model, group['content'], system_prompt=system_prompt(arm), capture=capture)
        record = validated_record(group, arm, draft, receipt, request_id, started, timestamp(), round((time.monotonic()-clock)*1000))
        save(attempt / 'validated.json', record)
        save(saved, record)
    except ValueError as exc:
        # Never copy unrecognized exceptions, provider body, or PRD fragments to public summaries.
        reason = str(exc) if re.fullmatch('[a-z][a-z0-9_]{1,70}',str(exc)) else 'invalid_response'
        record = dict(report=dict(variant=arm, status='failed', error=reason, started_at=started,
                                  completed_at=timestamp(), elapsed_ms=round((time.monotonic()-clock)*1000)))
        save(attempt / 'failure.json', record)
    return record


def fingerprint(item):
    text = unicodedata.normalize('NFKC', encode([item['steps'],item['expected']]).decode()).casefold()
    return digest(''.join(c for c in text if c.isalnum()).encode())


def case_rows(gid, result):
    point_refs = {point['id']:point['refs'] for point in result['points']}
    for case in result['cases']:
        refs = sorted({ref for pid in case['point_ids'] for ref in point_refs[pid]})
        yield dict(id=gid+'-'+case['id'], group=gid, title=case['title'], precondition=case['precondition'],
                   steps=case['steps'], expected=case['expected'], refs=refs)


def publish(out, groups, plan, records):
    keyfile = out / 'private/blind-seed'
    if not keyfile.exists():
        write(keyfile, secrets.token_bytes(32))
    key = keyfile.read_bytes()
    selected, blind, labels = [], [], {}
    seen = set()
    duplicate_ids = []
    report_groups = []
    for gid,g in groups.items():
        variants = []
        per_group = dict(id=gid, title=g['title'], clauses=g['clauses'], prototypes=g['prototypes'], input_sha256=g['input_sha256'], sample_count=g['sample_count'],
                         prototype_count=g['prototype_count'], variants=variants, observations=[], questions=[])
        for arm in ARMS:
            rec = records.get((gid,arm), {'report':dict(variant=arm,status='pending')})
            summary = dict(rec['report'])
            attempts = list((out / 'private' / gid / arm).glob('attempt-*'))
            summary['attempt_count'] = len(attempts)
            summary['rejected_attempts'] = []
            for attempt in sorted(attempts):
                failure = attempt / 'failure.json'
                if failure.exists():
                    prior = read(failure)['report']
                    item = {k: prior[k] for k in ('error', 'elapsed_ms', 'started_at') if k in prior}
                    metadata = attempt / 'response-metadata.json'
                    if metadata.exists():
                        item['request_id'] = read(metadata).get('request_id')
                    summary['rejected_attempts'].append(item)
            variants.append(summary)
            if summary['status'] != 'completed':
                continue
            draft = rec['result']
            if arm == 'few':
                per_group.update(observations=rec['observations'], questions=draft['questions'])
            for item in case_rows(gid,draft):
                sig = fingerprint(item)
                if arm == 'few':
                    if sig in seen:
                        duplicate_ids.append(item['id'])
                    else:
                        selected.append(item)
                        seen.add(sig)
                code = hmac.new(key, encode([arm, gid, item['id'], sig]), 'sha256').hexdigest()
                label = 'B-'+code[:12]
                labels[label] = dict(group=gid, variant=arm, case_id=item['id'])
                blind.append(dict(label=label, **{k:v for k,v in item.items() if k!='id'}, review=None))
        served_models = {r['model'] for r in variants if r['status'] == 'completed'}
        per_group['model_comparable'] = len(served_models) == 1 and all(r['status'] == 'completed' for r in variants)
        report_groups.append(per_group)
    blind.sort(key=lambda item:item['label'])
    complete = all(g['model_comparable'] for g in report_groups)
    report = dict(version=1, prompt_version=VERSION, status='complete' if complete else 'partial',
                  selected_variant='few', requested_model=plan['requested_model'], parameters=plan['parameters'],
                  prompt_sha256=plan['prompt_sha256'], sample_count=sum(g['sample_count'] for g in report_groups),
                  prototype_count=sum(g['prototype_count'] for g in report_groups), groups=report_groups,
                  candidate_count=len(selected), candidate_target=20, target_met=len(selected)>=20,
                  deduplication='NFKC 后忽略大小写与符号，按步骤与预期的规范化文本完全一致去重；语义重复待人工评估。',
                  duplicate_ids=duplicate_ids, candidates=selected, human_review='pending',
                  blind_review=dict(status='pending', candidate_count=len(blind), metrics=None))
    save(out / 'private/blind-key.json', labels)
    sources = [{k:g[k] for k in ('id','title','clauses','prototypes')} for g in report_groups]
    save(out / 'blind-review.json', dict(version=1,status='pending',sources=sources,items=blind))
    save(out / 'report.json', report)
    return report


def evaluate(inputs, out, replay_only=False):
    with output_lock(out):
        groups = load_groups(inputs)
        model = design.dashscope_vl_model()
        plan = freeze(out, groups, model)
        records = {}
        for number,(gid,group) in enumerate(groups.items()):
            for arm in (ARMS if number % 2 == 0 else reversed(ARMS)):
                record = replay_arm(out, gid, group, arm) if replay_only else run_arm(out, gid, group, arm, model)
                records[gid,arm] = record
                print(gid, arm, record['report']['status'], record['report'].get('error',''), flush=True)
        return publish(out, groups, plan, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-only', action='store_true', help='Validate existing receipts without sending model requests')
    args = parser.parse_args()
    result = evaluate(args.inputs, args.output, args.replay_only)
    print('candidate_count',result['candidate_count'],'of',result['candidate_target'],flush=True)
    return 0 if result['status']=='complete' and result['target_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
