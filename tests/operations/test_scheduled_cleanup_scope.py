import pytest

from scripts.scheduled_cleanup_scope import resolve


def row(job_id, kind, values):
    return {'id': job_id, 'project_id': 'p', 'target_type': kind, 'targets': values}


def graph():
    versions = {
        'v1': {'project_id': 'p', 'endpoint_id': 'e1', 'dependency_spec': {'dependencies': [{'case_version_id': 'v2', 'required': False}]}},
        'v2': {'project_id': 'p', 'endpoint_id': 'e2', 'dependency_spec': {}},
        'v3': {'project_id': 'p', 'endpoint_id': 'e3', 'dependency_spec': {}},
    }
    baselines = {'b1': {'project_id': 'p', 'version_id': 'v1', 'status': 'active', 'group_name': 'g'},
                 'b2': {'project_id': 'p', 'version_id': 'v3', 'status': 'active', 'group_name': 'h'}}
    tasks = {'t': {'project_id': 'p', 'selected_endpoint_ids': ['e3']}}
    return versions, baselines, tasks


def test_four_target_kinds_and_optional_recursive_dependency():
    versions, baselines, tasks = graph()
    jobs = [row('j1', 'cases', ['v1']), row('j2', 'baselines', ['b1']),
            row('j3', 'baseline_group', ['g']), row('j4', 'task', ['t'])]
    result = resolve(jobs, tasks, baselines, versions)
    assert result == {'j1': {'v1', 'v2'}, 'j2': {'v1', 'v2'},
                      'j3': {'v1', 'v2'}, 'j4': {'v3'}}


@pytest.mark.parametrize('failure', ['missing', 'cycle', 'other_project', 'empty', 'unknown'])
def test_incomplete_scope_never_reports_complete(failure):
    versions, baselines, tasks = graph()
    job = row('j', 'cases', ['v1'])
    if failure == 'missing': del versions['v2']
    elif failure == 'cycle': versions['v2']['dependency_spec'] = {'dependencies': [{'case_version_id': 'v1'}]}
    elif failure == 'other_project': versions['v2']['project_id'] = 'q'
    elif failure == 'empty': job['targets'] = []
    else: job['target_type'] = 'unknown'
    with pytest.raises(ValueError): resolve([job], tasks, baselines, versions)


def test_task_does_not_silently_discard_a_baseline_with_missing_version():
    versions, baselines, tasks = graph()
    del versions['v3']
    with pytest.raises(ValueError): resolve([row('j', 'task', ['t'])], tasks, baselines, versions)


def test_empty_group_is_reported_as_unresolved():
    versions, baselines, tasks = graph()
    with pytest.raises(ValueError): resolve([row('j', 'baseline_group', ['unknown'])], tasks, baselines, versions)


def test_isolated_postgres_is_readonly_and_outputs_no_internal_content():
    import json
    import os
    import psycopg
    from psycopg.types.json import Jsonb
    from scripts.scheduled_cleanup_scope import inventory
    dsn = os.environ.get('SCHEDULE_SCOPE_TEST_DSN')
    if not dsn:
        pytest.skip('requires isolated temporary PostgreSQL')
    vid1, vid2 = ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002')
    jid = '10000000-0000-0000-0000-000000000001'
    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute('CREATE TABLE api_scheduled_jobs(id text, project_id text, target_type text, enabled boolean)')
        setup.execute('CREATE TABLE api_scheduled_job_targets(job_id text, sequence int, target_type text, target_id text)')
        setup.execute('CREATE TABLE api_test_tasks(id text, project_id text, selected_endpoint_ids jsonb)')
        setup.execute('CREATE TABLE api_baselines(id text, project_id text, case_version_id text, status text, group_name text)')
        setup.execute('CREATE TABLE api_cases(id text, project_id text)')
        setup.execute('CREATE TABLE api_case_versions(id text, case_id text, endpoint_id text, dependency_spec jsonb, processing_spec jsonb)')
        setup.execute('INSERT INTO api_scheduled_jobs VALUES (%s, %s, %s, true)', (jid, 'p', 'cases'))
        setup.execute('INSERT INTO api_scheduled_job_targets VALUES (%s, 0, %s, %s)', (jid, 'cases', vid1))
        setup.execute('INSERT INTO api_cases VALUES (%s, %s), (%s, %s)', ('c1', 'p', 'c2', 'p'))
        setup.execute('INSERT INTO api_case_versions VALUES (%s, %s, %s, %s, %s)',
                      (vid1, 'c1', 'e1', Jsonb({'dependencies': [{'case_version_id': vid2}]}),
                       Jsonb({'cleanup_steps': [{'extractions': [{'target': 'PRIVATE_VALUE'}]}, {}]})))
        setup.execute('INSERT INTO api_case_versions VALUES (%s, %s, %s, %s, %s)',
                      (vid2, 'c2', 'e2', Jsonb({}), Jsonb({})))
    with psycopg.connect(dsn) as connection:
        class Probe:
            def transaction(self):
                return connection.transaction()
            def execute(self, sql):
                if sql.startswith('SELECT j.id'):
                    assert connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
                    connection.execute('SAVEPOINT readonly_probe')
                    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                        connection.execute("INSERT INTO api_cases VALUES ('no-write', 'p')")
                    connection.execute('ROLLBACK TO SAVEPOINT readonly_probe')
                return connection.execute(sql)
        result = inventory(Probe())
    assert result['schedule_count'] == 1
    assert len(result['schedules'][0]['versions']) == 2
    assert result['permissions_resolved'] is False
    assert result['schedules'][0]['versions'][0]['earlier_output']
    assert 'PRIVATE_VALUE' not in json.dumps(result)
