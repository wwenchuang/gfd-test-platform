import json

import pytest

from scripts.scheduled_cleanup_diagnostics import cleanup_structure, trace_summary, scheduled_hits, safe_name


def test_structural_summary_never_exposes_names_values_or_requests():
    secret = 'SECRET_DO_NOT_PRINT'
    item = cleanup_structure({'cleanup_steps': [{'name': secret, 'only_if_variable': secret,
        'request': {'method': 'DELETE', 'path': secret, 'body': secret},
        'extractions': [{'target': secret, 'default': secret, 'required': False}],
        'polling': {'max_attempts': 2}}, {'enabled': False}]})
    assert item['steps'][0] == {'index': 0, 'enabled': True, 'method': 'DELETE',
        'conditional': True, 'extraction_count': 1, 'optional_or_default_count': 1, 'poll_max_attempts': 2}
    assert secret not in json.dumps(item)


def test_trace_whitelist_suppresses_values_and_recognizes_retry_and_unknown():
    secret = 'SECRET_DO_NOT_PRINT'
    result = trace_summary({'trace': [
        {'phase': 'workflow_step', 'stage': 'cleanup', 'index': 0, 'status': 'FAILED', 'attempt': 1,
         'request': {'body': secret}, 'error_message': secret, 'extracted_variables': {}},
        {'phase': 'workflow_step', 'stage': 'cleanup', 'index': 0, 'status': 'PASSED', 'attempt': 2,
         'extracted_variables': {'token': '***', 'x': secret}},
        {'phase': 'workflow_step', 'stage': 'cleanup', 'index': 1, 'status': 'SKIPPED',
         'failure_category': 'condition_not_met', 'extracted_variables': {}},
    ]})
    assert result[0]['attempts'][1]['output_state'] == 'unknown_redacted'
    assert result[1]['attempts'][0]['status'] == 'SKIPPED'
    assert secret not in json.dumps(result)
    assert trace_summary({}) is None


def test_hits_require_expected_scope_and_exact_boolean():
    report = {'scope':'enabled_schedules_access_unfiltered_superset', 'permissions_resolved': False,
        'schedules':[{'job_id':'10000000-0000-0000-0000-000000000001', 'versions':[
            {'case_version_id':'00000000-0000-0000-0000-000000000001', 'earlier_output':True, 'indeterminate':False},
            {'case_version_id':'00000000-0000-0000-0000-000000000002', 'earlier_output':False, 'indeterminate':False}]}]}
    assert len(scheduled_hits(report)) == 1
    report['schedules'][0]['versions'][1]['earlier_output'] = 'true'
    with pytest.raises(ValueError): scheduled_hits(report)


def test_case_name_reduces_common_credential_and_number_exposure():
    name = safe_name('订单 password=topsecret 13800138000 https://example.test/path')
    assert 'topsecret' not in name and '13800138000' not in name and 'example.test' not in name


def test_isolated_postgres_exact_schedule_version_and_readonly():
    import os
    import psycopg
    from psycopg.types.json import Jsonb
    from scripts.scheduled_cleanup_diagnostics import diagnose
    dsn = os.environ.get('SCHEDULE_DIAGNOSTICS_TEST_DSN')
    if not dsn:
        pytest.skip('requires isolated PostgreSQL')
    job = '10000000-0000-0000-0000-000000000001'
    other_job = '10000000-0000-0000-0000-000000000002'
    version = '00000000-0000-0000-0000-000000000001'
    report = {'scope':'enabled_schedules_access_unfiltered_superset', 'permissions_resolved':False,
              'schedules':[{'job_id':job, 'versions':[{'case_version_id':version, 'earlier_output':True, 'indeterminate':False}]}]}
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute('CREATE TABLE api_cases(id text, name text)')
        c.execute('CREATE TABLE api_case_versions(id text, case_id text, processing_spec jsonb)')
        c.execute('CREATE TABLE api_scheduled_job_runs(job_id text, execution_id text)')
        c.execute('CREATE TABLE api_executions(id text, state text, created_at timestamptz)')
        c.execute('CREATE TABLE api_execution_cases(id text, execution_id text, case_version_id text, status text, created_at timestamptz, sanitized_result jsonb)')
        c.execute('INSERT INTO api_cases VALUES (%s, %s)', ('case', 'safe case'))
        c.execute('INSERT INTO api_case_versions VALUES (%s,%s,%s)', (version,'case',Jsonb({'cleanup_steps':[{'request':{'method':'GET','body':'SECRET'},'extractions':[{'target':'SECRET'}]},{}]})))
        for suffix, owner, date in [('1',job,'2026-01-01'),('2',job,'2026-01-02'),('3',other_job,'2026-01-03')]:
            execution = '20000000-0000-0000-0000-' + suffix.zfill(12)
            case = '30000000-0000-0000-0000-' + suffix.zfill(12)
            c.execute('INSERT INTO api_scheduled_job_runs VALUES (%s,%s)', (owner,execution))
            c.execute('INSERT INTO api_executions VALUES (%s,%s,%s)', (execution,'DONE',date))
            c.execute('INSERT INTO api_execution_cases VALUES (%s,%s,%s,%s,%s,%s)',
                      (case,execution,version,'PASSED',date,Jsonb({'trace':[{'phase':'workflow_step','stage':'cleanup','index':0,'status':'PASSED','request':{'body':'SECRET'},'extracted_variables':{'resource':'SECRET'}}]})))
    with psycopg.connect(dsn) as c:
        class Probe:
            def transaction(self): return c.transaction()
            def execute(self, sql, params=None):
                if sql.startswith('SELECT c.name'):
                    assert c.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
                    c.execute('SAVEPOINT probe')
                    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                        c.execute("INSERT INTO api_cases VALUES ('x','x')")
                    c.execute('ROLLBACK TO SAVEPOINT probe')
                return c.execute(sql, params)
        result = diagnose(Probe(), report)
    assert result['count'] == 1
    evidence = result['items'][0]['latest_scheduled_history']
    assert evidence['execution_id'].endswith('000000000002')
    assert evidence['cleanup_trace'][0]['attempts'][0]['output_state'] == 'nonempty_recorded'
    assert 'SECRET' not in json.dumps(result)
