"""Owner-scoped facts and incident history for the fixed workbench suites."""
from collections import defaultdict
from math import isfinite
import re
import uuid

from task_server.services import platform_mcp

_WINDOW = 80
_TERMINAL = {'passed','failed','cancelled','blocked','interrupted','timed_out'}
_ERRORS = {'blocked','interrupted','timed_out'}
_ACTIVE = {'queued','running','cancelling'}
_KINDS = {'authentication','business','browser_timeout','cancelled','check_busy','drill_assertion',
          'environment','evidence','execution_disabled','heavy_workload_busy','invalid_runner_result',
          'service_restarted','timeout','worker_crash','worker_timeout','worker_unavailable'}
_DRILL = 'catalog-failure-drill'


def _uuid(value):
    try:
        return isinstance(value,str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _timestamp(value):
    return value if type(value) in (float,int) and 0 <= value < 32503680000 and isfinite(value) else None


def _rows(actor):
    # history enforces current session, owner, application scope and feedback project access.
    runs = {}
    for row in platform_mcp.history(actor)[:_WINDOW]:
        if (_uuid(row['run_id']) and row['suite_id'] in platform_mcp.SUITES and row['environment']=='platform'
                and row['status'] in _TERMINAL | _ACTIVE):
            runs.setdefault(row['run_id'],row)
    return list(runs.values())


def _attempts(row):
    expected_runner, expected_case = platform_mcp.SUITES[row['suite_id']][1:]
    observed = {}
    for case in row['cases']:
        n = case.get('attempt')
        if (case.get('run_id') == row['run_id'] and case.get('case_id') == expected_case and
                case.get('runner') == expected_runner and case.get('status') in _TERMINAL and
                type(n) is int and n > 0):
            observed[n] = case
    return [observed[key] for key in sorted(observed)]


def _rate(passed, total):
    return {'numerator':passed, 'denominator':total, 'value':round(100*passed/total,2) if total else None,
            'note':'没有符合口径的样本' if not total else '仅 1 个样本' if total == 1 else f'窗口内 {total} 个样本'}


def _incidents(events):
    active = defaultdict(dict)
    incidents = []
    failures_at = defaultdict(set)
    for clock,rid,_,case,_ in events:
        if case['status'] in _ERRORS | {'failed'}:
            failures_at[clock,case['case_id']].add(rid)
    for clock,rid,_,case,observed_at in sorted(events,key=lambda event:event[:3]):
        case_id = case['case_id']
        if case['status'] == 'passed':
            # Equal clocks across independent runs do not establish a later success.
            if failures_at[clock,case_id] - {rid}:
                continue
            for event in active.pop(case_id,{}).values():
                duration = None
                if observed_at is not None and event['opened_at'] is not None:
                    duration = round(1000*(observed_at-event['opened_at']))
                event.update(status='recovered', recovery_run_id=rid, recovered_at=observed_at, recovery_ms=duration)
        elif case['status'] in _ERRORS | {'failed'}:
            kind = case.get('error_kind')
            kind = kind if kind in _KINDS else 'unknown'
            event = active[case_id].get(kind)
            if event is None:
                event = {'fingerprint':f'{case_id}/{kind}', 'case_id':case_id,'error_kind':kind,'status':'open',
                         'opened_at':observed_at,'last_failed_at':None,'recovered_at':None,'recovery_ms':None,
                         'run_ids':[],'occurrences':0,'recovery_run_id':None}
                active[case_id][kind] = event
                incidents.append(event)
            event['last_failed_at'] = observed_at
            event['occurrences'] += 1
            if rid not in event['run_ids']:
                event['run_ids'].append(rid)
    return list(reversed(incidents))


def _artifact_facts(row,evidence):
    rid = row.get('runner_id')
    if not isinstance(rid,str) or not re.fullmatch('[0-9a-f]{32}',rid):
        return {}
    return {'artifact.'+key:'available' for key in ('screenshot','trace')
            if evidence.get(key) == f'/api/ui-executions/web-check/artifact?id={rid}&item={key}'}


def report(actor):
    rows = _rows(actor)
    runs, events, durations, gold = [], [], [], []
    first_ok = first_total = retried_fail = retry_ok = retry_samples = drill_count = active_count = 0
    counts = dict(passed=0,failed=0,errors=0,skipped=0,total=0)
    for row in rows:
        rid, suite, status = row['run_id'], row['suite_id'], row['status']
        finished = _timestamp(row['finished_at'])
        drill = suite == _DRILL
        runs.append(dict(run_id=rid,suite_id=suite,status=status,is_drill=drill,finished_at=finished))
        active_count += status in _ACTIVE
        drill_count += drill
        if drill or status in _ACTIVE:
            continue
        attempts = _attempts(row)
        if not attempts:
            continue
        first, last = attempts[0], attempts[-1]
        has_first = first['attempt'] == 1
        first_total += has_first
        first_ok += has_first and first['status']=='passed'
        final = last['status']
        if len(gold) < 20 and final in _ERRORS | {'failed'}:
            refs = ['run.id','case.id','case.status']
            if last.get('error_kind') in _KINDS:
                refs.append('case.error_kind')
            observed = last.get('evidence') or {}
            proof = []
            if type(last.get('worker_exit_code')) is int:
                proof.append('case.worker_exit_code')
            if suite == 'feedback-categories' and type(observed.get('http_status')) is int:
                proof.append('observed.http_status')
            if suite == 'published-catalog':
                proof.extend(_artifact_facts(row,observed))
                if type(observed.get('total')) is int and observed['total']>=0:
                    proof.append('observed.total')
            if proof:
                gold.append(dict(run_id=rid,suite_id=suite,case_id=last['case_id'],case_title=platform_mcp.SUITES[suite][0],
                                 status=final,error_kind=last['error_kind'] if 'case.error_kind' in refs else 'unknown',
                                 refs=refs+proof))
        counts['total'] += 1
        counts['errors' if final in _ERRORS else 'skipped' if final=='cancelled' else final] += 1
        if has_first and len(attempts)>1:
            retry_samples += 1
            if first['status'] in _ERRORS | {'failed'}:
                retried_fail += 1
                retry_ok += final == 'passed'
        for case in attempts:
            duration = case['duration_ms']
            if type(duration) is int and duration >= 0:
                durations.append(duration)
            # W11 reports a run completion time. Its earlier attempts, if any, have no individual timestamp.
            stamp = _timestamp(case.get('finished_at'))
            if stamp is None and case is last:
                stamp = finished
            if finished is not None:
                events.append((stamp if stamp is not None else finished,rid,case['attempt'],case,stamp))
    incidents = _incidents(events)
    recovered = [i['recovery_ms'] for i in incidents if i['recovery_ms'] is not None]
    metrics = dict(counts=counts,first_pass_rate=_rate(first_ok,first_total),
                   final_pass_rate=_rate(counts['passed'],counts['total']),
                   retry_recovery_rate=_rate(retry_ok,retried_fail),flake_rate=_rate(retry_ok,retry_samples),
                   duration=dict(sample_count=len(durations),total_ms=sum(durations),
                                 mean_ms=round(sum(durations)/len(durations),2) if durations else None),
                   average_recovery_ms=round(sum(recovered)/len(recovered),2) if recovered else None)
    return dict(window_limit=_WINDOW,history_count=len(rows),drill_runs=drill_count,active_runs=active_count,
                runs=runs,incidents=incidents,metrics=metrics,gold_candidates=gold,methodology=[
                    '窗口取本人当前有权查看的最近 80 次运行。正式通过率、错误、耗时和候选样本均排除故意失败演练。',
                    '首轮分母为已观察到第 1 次尝试的业务终态用例数；末次分母为有终态尝试的业务用例数。分子均为对应尝试通过数。',
                    '按运行和用例编号识别同一个测试。独立运行使用新的分母；同一次请求的幂等回读只算一次。',
                    '通过、业务失败、取消跳过和执行错误均计入末次分母；排队、运行中和取消中暂不计入。',
                    '耗时合计和平均值计入每次有耗时记录的业务尝试。恢复时长取本窗口内首次失败到后续同用例成功的观测时间差。'])


def evidence_bundle(actor, run_id):
    row = next((r for r in _rows(actor) if r['run_id'] == run_id),None)
    if not row:
        raise platform_mcp.ToolError(404,'run_not_found','执行不存在或无权查看')
    suite, status = row['suite_id'], row['status']
    drill = suite == _DRILL
    facts = {'run.id':run_id,'run.suite':suite,'run.status':status,'run.environment':'platform',
             'run.drill':'true' if drill else 'false'}
    attempts = _attempts(row)
    if attempts and status in _TERMINAL:
        case = attempts[-1]
        facts.update({'case.id':case['case_id'],'case.runner':case['runner'],'case.status':case['status'],
                      'case.attempt':str(case['attempt'])})
        if case.get('error_kind') in _KINDS:
            facts['case.error_kind'] = case['error_kind']
        version = case.get('version')
        if isinstance(version,str) and re.fullmatch(r'v?[0-9]{1,3}(?:\.[0-9]{1,4}){1,3}',version):
            facts['case.version'] = version
        for field in ('exit_code','worker_exit_code','duration_ms'):
            if type(case.get(field)) is int:
                facts['case.'+field] = str(case[field])
        evidence = case.get('evidence') or {}
        if suite == 'feedback-categories':
            for key in ('actual_code','expected_code','http_status'):
                if type(evidence.get(key)) is int:
                    facts['observed.'+key] = str(evidence[key])
            for key in ('data_is_list','http_200','code_zero','data_list'):
                source = evidence if key == 'data_is_list' else evidence.get('checks')
                if isinstance(source,dict) and type(source.get(key)) is bool:
                    facts['observed.'+key] = str(source[key]).lower()
        else:
            for key in ('total','expected_total'):
                if type(evidence.get(key)) is int and evidence[key] >= 0:
                    facts['observed.'+key] = str(evidence[key])
            facts.update(_artifact_facts(row,evidence))
    included = int('case.status' in facts)
    return dict(run_id=run_id,suite_id=suite,is_drill=drill,facts=facts,
                started_at=_timestamp(row.get('started_at')),finished_at=_timestamp(row.get('finished_at')),
                coverage=dict(source='structured_case_result',attempts_observed=len(attempts),attempts_included=included,
                              truncated=len(attempts)>included,raw_logs_included=False,source_logs_truncated=None))


def defect_draft(actor, run_id):
    bundle = evidence_bundle(actor,run_id)
    facts, suite, drill = bundle['facts'], bundle['suite_id'], bundle['is_drill']
    status = facts.get('case.status')
    if status not in _ERRORS | {'failed'}:
        raise platform_mcp.ToolError(409,'run_not_failed','此运行没有可起草的失败用例')
    title = platform_mcp.SUITES[suite][0]
    expected = []
    actual = [f"用例终态 {status}，平台记录的原因是 {facts.get('case.error_kind','unknown')}。"]
    if status in _ERRORS:
        expected.append('执行器完成检查并返回业务断言结果。')
        actual.append('此次执行没有完成业务断言。')
    elif suite == 'feedback-categories':
        expected.append('HTTP 状态为 200，业务码为 0，data 是列表。')
        for key,label in [('http_status','HTTP 状态'),('actual_code','业务码'),('data_is_list','data 为列表')]:
            if 'observed.'+key in facts:
                actual.append(f"{label}为 {facts['observed.'+key]}。")
    else:
        expected.append('页面的已发布用例数量与接口返回的数量一致。' if not drill else '演练通过故意设置的数量差异来验证失败报告。')
        if 'observed.expected_total' in facts:
            expected.append(f"本次断言使用的预期数量为 {facts['observed.expected_total']}。")
        if 'observed.total' in facts:
            actual.append(f"本次记录的数量为 {facts['observed.total']}。")
    return dict(run_id=run_id,is_drill=drill,title=f'{title} · {status}',
                facts=[f"运行 {run_id}，用例 {facts['case.id']}，第 {facts['case.attempt']} 次执行。"],
                reproduction=['用原权限进入测试工作台的工具执行。',f'选择「{title}」，使用新请求号执行并核对本次结果。'],
                expected=expected,actual=actual,to_confirm=['由人工确认复现情况、影响范围、严重性和根因。'],
                refs=list(facts))
