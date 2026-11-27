import json
from pathlib import Path

from runner_monkey_job import execute


def job():
    return dict(job_id='mon_a', job_type='ui_monkey', runner_id='runner', device_id='phone',
                platform='android', app_id='logical.test', target_package='com.test.app',
                event_count=2, seed=0, monkey_claim_token='ticket', mode='browse')


def test_final_and_scope(tmp_path):
    driver = tmp_path/'driver.py'
    driver.write_text("""import json,sys
r=json.load(sys.stdin)
assert r['app_id']=='com.test.app' and r['seed']==0
print(json.dumps(dict(type='progress',executed=1,message='第一个动作')))
print(json.dumps(dict(type='result',status='crashed',executed=2,message='应用崩溃',log='已观察两个操作',crash_log='E/FATAL stack 1')))
""")
    calls = []
    def progress(payload):
        calls.append(payload)
        return {'ok': True}
    result = execute(job(), 'runner', 'android', driver, progress)
    assert result['status']=='crashed' and result['crash_log']=='E/FATAL stack 1'
    assert result['executed']==2 and result['process_stopped']
    assert calls and all(p['device_id']=='phone' and p['monkey_claim_token']=='ticket' for p in calls)
    assert execute(job(), 'wrong', 'android', driver, progress)['status']=='failed'


def test_stop_before_launch(tmp_path):
    result=execute(job(), 'runner', 'android', tmp_path/'never-started', lambda p:{'stop_requested':True})
    assert result['status']=='stopped' and result['executed']==0 and result['process_stopped']


def test_restart_waits_for_the_driver_to_release_its_lock(tmp_path):
    import fcntl
    from runner_monkey_job import _journal, _write, recover
    path = _journal(tmp_path, job()['job_id'])
    path.parent.mkdir()
    _write(path, {'job_id':job()['job_id'], 'payload':{'executed':1, 'monkey_claim_token':'ticket'}})
    sent=[]
    with path.with_suffix('.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        recover(tmp_path,lambda *pair:sent.append(pair))
        assert path.exists() and not sent
    recover(tmp_path,lambda *pair:sent.append(pair))
    assert sent[0][1]['status']=='inconclusive' and sent[0][1]['executed']==1
    assert sent[0][1]['process_stopped'] and not path.exists()


def test_duration_requires_elapsed_time_and_real_events(tmp_path):
    config = job()
    del config['event_count']
    config['duration_seconds'] = 60
    driver = tmp_path / 'driver.py'
    for elapsed, events, expected in [(60, 15, 'passed'), (59, 15, 'inconclusive'), (60, 0, 'inconclusive')]:
        result = dict(type='result', status='passed', elapsed_seconds=elapsed, executed=events)
        driver.write_text("import json,sys\nconfig=json.load(sys.stdin)\n"
                          "assert config['duration_seconds']==60 and 'event_count' not in config\n"
                          + "print(" + repr(json.dumps(result)) + ")")
        observed = execute(config, 'runner', 'android', driver, lambda body: {})
        assert observed['status'] == expected
        assert observed['elapsed_seconds'] == elapsed
        assert observed['executed'] == events
