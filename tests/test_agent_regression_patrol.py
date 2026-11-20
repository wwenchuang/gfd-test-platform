from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.services import agent_patrol as patrol


def patrol_runs(rows):
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE api_executions (id, created_by, state, finished_at)')
        db.exec_driver_sql('CREATE TABLE api_execution_cases (id, execution_id, status)')
        db.exec_driver_sql('CREATE TABLE api_scheduled_job_runs (execution_id, job_id, trigger_type)')
        for run, job, trigger, status in rows:
            db.exec_driver_sql('INSERT INTO api_executions VALUES (?, ?, ?, ?)',
                               (run, 'owner', 'DONE', datetime.now(timezone.utc).isoformat()))
            db.exec_driver_sql('INSERT INTO api_execution_cases VALUES (?, ?, ?)', (run, run, status))
            db.exec_driver_sql('INSERT INTO api_scheduled_job_runs VALUES (?, ?, ?)', (run, job, trigger))
    return sessionmaker(engine)


def test_patrol_filters_runs_and_reuses_owner_investigation(monkeypatch):
    factory = patrol_runs([('target', 'a', 'schedule', 'FAILED'), ('manual', 'a', 'manual', 'FAILED'),
                           ('pass', 'a', 'schedule', 'PASSED'), ('other', 'b', 'schedule', 'FAILED')])
    notices = []
    monkeypatch.setattr("task_server.api_testing.services.patrol_summary.publish", lambda factory, rid, *args: notices.append(rid))
    started = {}
    actor = dict(kind='user', username='owner', user_id='1', display_name='Owner')
    monkeypatch.setattr(patrol, 'actor_from_trusted_username', lambda name: actor if name == 'owner' else {})
    monkeypatch.setattr(patrol.investigations, 'latest', lambda actor, source, rid: started.get(rid))
    def start(actor, body):
        assert body['run_id'] not in started
        started[body['run_id']] = dict(body, status='completed')
        return started[body['run_id']]
    monkeypatch.setattr(patrol.investigations, 'start', start)
    for _ in range(2):
        patrol.scan(factory, ['a'])
    assert list(started) == ['target']


def test_comparison_requires_explicit_pair():
    from types import SimpleNamespace
    from task_server.api_testing.services.patrol_summary import compare
    def rows(statuses):
        return [SimpleNamespace(id=str(i), case_version_id=i, status=s) for i,s in enumerate(statuses)]
    result = compare(rows(['FAILED', 'BROKEN', 'PASSED', 'SKIPPED']), rows(['PASSED', 'FAILED', 'FAILED', 'FAILED']))
    assert [len(v) for v in result.values()] == [1, 1, 1, 1]


def test_already_finished_repair_is_published(monkeypatch):
    """The idempotent repair can finish limited with the same evidence gap."""
    factory = patrol_runs([('run', 'home', 'schedule', 'FAILED')])
    actor = dict(kind='user', username='owner', user_id='1', display_name='Owner')
    limited = dict(id='repaired', status='limited', evidence=[], result={'missing_evidence': ['secret_context_incomplete']})
    monkeypatch.setattr(patrol, 'actor_from_trusted_username', lambda name: actor)
    monkeypatch.setattr(patrol.investigations, 'latest', lambda *_: limited)
    monkeypatch.setattr(patrol.investigations, 'start', lambda *_: limited)
    published = []
    monkeypatch.setattr('task_server.api_testing.services.patrol_summary.publish',
                        lambda factory, rid, username, jobs, task: published.append(task['id']))
    patrol.scan(factory, ['home'])
    assert published == ['repaired']
