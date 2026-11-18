"""Real queue + ORM evidence + Agent protocol; only the model is a local fixture."""
import json
import time
import uuid

from test_failure_analysis_sources import api
from task_server.background_jobs import PersistedJobDispatcher
from task_server.services.operation_attribution import actor_context, current_actor


def test_real_queue_reads_scoped_sql_evidence_and_returns_cited_conclusion(api, tmp_path, monkeypatch):
    from task_server.services import failure_analysis_tasks as tasks, failure_analysis_model as model
    from task_server.services.failure_analysis_sources import EvidenceSource
    monkeypatch.setenv('TASK_AUTH_DB', str(tmp_path / 'identity.sqlite3'))
    monkeypatch.setattr(tasks, '_source', lambda actor, source, rid: EvidenceSource(actor, source, rid, session_factory=api.factory))
    dispatcher = PersistedJobDispatcher(1, 2, job_loader=tasks._load,
        runner_resolver=lambda kind: tasks._run, failure_recorder=tasks._finish_error)
    monkeypatch.setattr(tasks, '_dispatcher', dispatcher)
    calls = []
    def completion(**request):
        messages = request['messages']
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        calls.append(len(results))
        if len(results) < 2:
            name = 'get_failed_steps' if not results else 'get_request_trace'
            args = {} if not results else {'case_id': results[0]['cases'][0]['case_id']}
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call_{len(results)}',
                'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
            reason = 'tool_calls'
        else:
            observed = results[-1]['evidence'][0]
            assert observed['data']['response']['status_code'] == 200
            assert observed['data']['assertions'][0]['passed'] is False
            message = {'role': 'assistant', 'content': json.dumps({'summary': '接口收到响应，业务断言未通过。',
                'facts': [{'text': '响应状态为200，断言期望1而实际为0。', 'evidence_ids': [observed['id']]}],
                'hypotheses': [], 'recommendations': [{'text': '核对读取列表的数量规则及本次测试数据。', 'evidence_ids': [observed['id']]}],
                'missing_evidence': []}, ensure_ascii=False)}
            reason = 'stop'
        return {'choices': [{'message': message, 'finish_reason': reason}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}}
    monkeypatch.setattr(model, 'call_model', completion)
    try:
        with actor_context(api.actor):
            task = tasks.start(current_actor(), {'source': 'api', 'run_id': api.run_id, 'request_id': str(uuid.uuid4())})
            for _ in range(160):
                task = tasks.read(current_actor(), task['id'])
                if task['status'] not in tasks.ACTIVE:
                    break
                time.sleep(.025)
            assert task['status'] == 'completed', task
            assert calls == [0, 1, 2]
            assert len(task['evidence']) == 2 and task['rounds'] == 3
            assert task['result']['facts'][0]['evidence_ids'] == [task['evidence'][1]['id']]
            assert tasks.latest(current_actor(), 'api', api.run_id)['id'] == task['id']
    finally:
        dispatcher.stop()
