from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.services import agent_patrol as patrol


def test_patrol_filters_runs_and_reuses_owner_investigation(monkeypatch):
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE api_executions (id, created_by, state, finished_at)')
        db.exec_driver_sql('CREATE TABLE api_execution_cases (id, execution_id, status)')
        db.exec_driver_sql('CREATE TABLE api_scheduled_job_runs (execution_id, job_id, trigger_type)')
        for run, job, trigger, status in [('target','a','schedule','FAILED'), ('manual','a','manual','FAILED'),
                                          ('pass','a','schedule','PASSED'), ('other','b','schedule','FAILED')]:
            db.exec_driver_sql('INSERT INTO api_executions VALUES (?, ?, ?, ?)',
                               (run, 'owner', 'DONE', datetime.now(timezone.utc).isoformat()))
            db.exec_driver_sql('INSERT INTO api_execution_cases VALUES (?, ?, ?)', (run, run, status))
            db.exec_driver_sql('INSERT INTO api_scheduled_job_runs VALUES (?, ?, ?)', (run, job, trigger))
    started = {}
    actor = dict(kind='user', username='owner', user_id='1', display_name='Owner')
    monkeypatch.setattr(patrol, 'actor_from_trusted_username', lambda name: actor if name == 'owner' else {})
    monkeypatch.setattr(patrol.investigations, 'latest', lambda actor, source, rid: started.get(rid))
    def start(actor, body):
        assert body['run_id'] not in started
        started[body['run_id']] = body
    monkeypatch.setattr(patrol.investigations, 'start', start)
    for _ in range(2):
        patrol.scan(sessionmaker(engine), ['a'])
    assert list(started) == ['target']
