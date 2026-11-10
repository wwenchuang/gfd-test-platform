"""Stage a verified primary and supplemental Week 9 batch for workbench publication."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

PRIMARY = {'ui_case_selection', 'ui_case_revision', 'ui_start_handoff', 'ui_start_empty_guard', 'ui_catalog_paging'}
SEARCH = 'ui_catalog_search_restore'
USAGE = ('calls', 'reported_tokens', 'unreported_calls')
SHA = re.compile(r'[0-9a-f]{64}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_file(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as src:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, 'invalid input file')
        data = src.read(limit + 1)
        require(len(data) <= limit, 'input too large')
        return data


def write_file(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as dst:
        dst.write(data)


def copy_shots(run, folder, dest, checked):
    shots = run['screenshots']
    require(isinstance(shots, list), 'invalid screenshot list')
    if run['status'] == 'passed':
        labels = ({'填写前的用例目录','已填写真实基线名称','回车查询后，清单和目标版本已核对','清空后恢复原清单'}
                  if run['scenario_id'] == SEARCH else {'操作前','操作后'})
        require(labels <= {s['label'] for s in shots}, 'passed run missing step evidence')
    for shot in shots:
        h = shot['sha256']
        require(isinstance(h, str) and SHA.fullmatch(h) and shot['file'] == h+'.png', 'invalid screenshot reference')
        if h in checked:
            continue
        require(folder.is_dir() and not folder.is_symlink(), 'invalid visual artifacts directory')
        data = read_file(folder / (h+'.png'), 8 * 1024 * 1024)
        require(data.startswith(b'\x89PNG\r\n\x1a\n') and hashlib.sha256(data).hexdigest() == h, 'screenshot digest mismatch')
        if not (dest/(h+'.png')).exists():
            write_file(dest/(h+'.png'), data)
        checked.add(h)


def batch(source, expected, campaign, dest):
    data = read_file(source, 4 * 1024 * 1024)
    doc = json.loads(data)
    require(doc['version'] == 1 and doc['completed'] is True and doc['limits']['rounds'] == 10,
            'incomplete or incompatible batch')
    require(len(doc['scenarios']) == len(expected) and {s['id'] for s in doc['scenarios']} == expected,
            'unexpected scenarios')
    require(isinstance(doc['batch_id'], str) and doc['batch_id'] and SHA.fullmatch(doc['fingerprint']), 'invalid provenance')
    conditions = doc.get('conditions', {}) if campaign == 'primary' else {SEARCH:doc.get('condition_sha256')}
    require(isinstance(conditions, dict) and not set(conditions)-expected, 'invalid scenario conditions')
    remaining = {(s, a, n) for s in expected for a in ('control', 'ai') for n in range(1, 11)}
    usage = dict.fromkeys(USAGE, 0)
    checked = set()
    for run in doc['runs']:
        key = (run['scenario_id'], run['arm'], run['round'])
        require(type(key[2]) is int and key in remaining, 'unexpected or duplicate trial')
        remaining.remove(key)
        require(run['status'] in ('passed','failed','blocked') and run['reason'] != 'in_flight', 'unfinished trial')
        if run['status'] == 'passed':
            frozen = conditions.get(run['scenario_id'])
            require(isinstance(frozen, str) and SHA.fullmatch(frozen) and run.get('condition_sha256') == frozen
                    and run.get('actions', 0) > 0, 'passed trial without matching frozen conditions or actions')
        calls = [call for step in run['steps'] for call in step['calls']]
        require(len(calls) == run['model_calls'], 'model call count mismatch')
        require(len(calls) <= doc['limits']['model_calls_per_run'] and (run['arm'] == 'ai' or not calls),
                'trial used calls outside its budget or arm')
        tokens = [call['tokens'] for call in calls]
        require(all(t is None or (type(t) is int and t >= 0) for t in tokens), 'invalid token usage')
        require(run['tokens'] == (None if None in tokens else sum(tokens)), 'run token total mismatch')
        usage['calls'] += len(calls)
        usage['unreported_calls'] += tokens.count(None)
        usage['reported_tokens'] += sum(t for t in tokens if t is not None)
        copy_shots(run, source.parent/'visual-artifacts', dest, checked)
        run.update(campaign=campaign, source_batch_id=doc['batch_id'])
    require(not remaining and usage == doc['usage'], 'missing trials or batch usage mismatch')
    require(usage['calls'] <= doc['limits']['model_calls_total'], 'model call limit exceeded')
    doc['conditions'] = {k:v for k,v in conditions.items() if v is not None}
    for scenario in doc['scenarios']:
        scenario.update(campaign=campaign, source_batch_id=doc['batch_id'])
        if campaign == 'supplemental':
            scenario['title'] = '补充场景 · '+scenario['title']+'（每轮最多 '+str(doc['limits']['model_calls_per_run'])+' 次模型调用）'
    record = {k:doc[k] for k in ('batch_id','fingerprint','limits','usage','versions','model','viewport','started_at','generated_at')}
    record.update(campaign=campaign, report_sha256=hashlib.sha256(data).hexdigest(), trials=len(doc['runs']))
    return doc, record


def merge(primary, supplemental, output):
    require(not output.exists() and not output.is_symlink(), 'use a new staging directory')
    tmp = Path(tempfile.mkdtemp(prefix='.visual-merge-', dir=output.parent))
    try:
        assets = tmp/'visual-artifacts'
        assets.mkdir(mode=0o700)
        a, source_a = batch(primary, PRIMARY, 'primary', assets)
        b, source_b = batch(supplemental, {SEARCH}, 'supplemental', assets)
        require(a['batch_id'] != b['batch_id'] and a['fingerprint'] != b['fingerprint'], 'duplicate source batch')
        for key in ('model', 'family', 'viewport', 'versions'):
            require(a[key] == b[key], 'incompatible batches: '+key)
        for key in ('rounds','output_tokens_per_call'):
            require(a['limits'][key] == b['limits'][key], 'incompatible limit: '+key)
        doc = {key:a[key] for key in ('version','model','family','versions','viewport','started_at')}
        doc.update(batch_id=a['batch_id']+' / '+b['batch_id'], completed=True, generated_at=max(a['generated_at'],b['generated_at']),
                   scenarios=a['scenarios']+b['scenarios'], runs=a['runs']+b['runs'], sources=[source_a,source_b],
                   conditions={**a.get('conditions', {}), **b.get('conditions', {})},
                   limits={**a['limits'], 'model_calls_per_run':max(a['limits']['model_calls_per_run'],b['limits']['model_calls_per_run']),
                           'model_calls_total':a['limits']['model_calls_total']+b['limits']['model_calls_total']},
                   usage={k:a['usage'][k]+b['usage'][k] for k in USAGE})
        write_file(tmp/'w9-report.json', (json.dumps(doc,ensure_ascii=False,indent=2)+'\n').encode())
        tmp.rename(output)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('primary', type=Path, help='Primary w9-report.json')
    parser.add_argument('supplemental', type=Path, help='Supplemental search w9-report.json')
    parser.add_argument('output', type=Path, help='New staging directory with existing parent')
    args = parser.parse_args()
    merge(args.primary, args.supplemental, args.output)
    print('Verified 100 primary and 20 supplemental trials.')
