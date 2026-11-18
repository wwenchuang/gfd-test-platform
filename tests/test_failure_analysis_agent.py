"""Protocol fixtures exercise the real loop; they do not claim real Qwen acceptance."""
import copy
import importlib
import json

import pytest


def test_agent_entrypoint_exists():
    assert importlib.util.find_spec("task_server.services.failure_analysis_agent") is not None


@pytest.fixture
def agent():
    return importlib.import_module("task_server.services.failure_analysis_agent")


def answer(text="timeout", refs=None):
    return {"summary": "调查结果待人工核对", "facts": [{"text": text, "evidence_ids": refs or ["failure:1"]}],
            "hypotheses": [], "recommendations": [], "missing_evidence": []}


def response(content=None, calls=None, usage=True):
    msg = {"role": "assistant", "content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else content}
    if calls is not None:
        msg["tool_calls"] = calls
        msg["reasoning_content"] = "PRIVATE CHAIN OF THOUGHT"
    result = {"model": "qwen-test", "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}]}
    if usage:
        result["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    return result


def call(name="get_failed_steps", args=None, ident="call_1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


class Source:
    source = "api"
    run_id = "run-1"
    actor = {"user_id": "owner-1"}

    def __init__(self, request="502 upstream unavailable"):
        self.request = request
        self.calls = []
        self.auth_count = 0
        self.revoked = False

    def authorize(self):
        self.auth_count += 1
        if self.revoked:
            raise PermissionError("private revoked detail sk-secret")

    def get_failed_steps(self):
        self.calls.append("get_failed_steps")
        return {"evidence": [{"id": "failure:1", "kind": "failure", "title": "Failure", "href": "/report/1", "data": {"case_id": "case-1", "error": "timeout"}}], "missing": []}

    def get_request_trace(self, case_id):
        assert case_id == "case-1"
        self.calls.append("get_request_trace")
        return {"evidence": [{"id": "request:1", "kind": "request", "title": "Request", "href": "/report/1", "data": {"status": self.request}}], "missing": []}

    def compare_last_pass(self, case_id):
        assert case_id == "case-1"
        self.calls.append("compare_last_pass")
        return {"evidence": [{"id": "comparison:1", "kind": "comparison", "title": "Comparison", "href": "/report/1", "data": {"difference": "version changed"}}], "missing": []}


class Script:
    def __init__(self, *items):
        self.items = list(items)
        self.inputs = []

    def __call__(self, **kwargs):
        self.inputs.append(copy.deepcopy(kwargs))
        assert 0 < kwargs["timeout_seconds"] <= 45
        assert 0 < kwargs["max_tokens"] <= 2000
        return self.items.pop(0)


def test_model_selects_divergent_paths_from_same_timeout(agent):
    class AdaptiveProtocolModel:
        def __init__(self):
            self.inputs = []

        def __call__(self, **kwargs):
            self.inputs.append(copy.deepcopy(kwargs))
            results = [json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool"]
            if not results:
                return response(calls=[call()])
            if len(results) == 1:
                return response(calls=[call("get_request_trace", {"case_id": "case-1"}, "trace")])
            request = results[1]["evidence"][0]["data"]["status"]
            if request == "200 with unexpected payload" and len(results) == 2:
                return response(calls=[call("compare_last_pass", {"case_id": "case-1"}, "history")])
            return response(answer(request, ["request:1"]))

    for request, expected in [("502 upstream unavailable", ["get_failed_steps", "get_request_trace"]),
                              ("200 with unexpected payload", ["get_failed_steps", "get_request_trace", "compare_last_pass"])]:
        source, model = Source(request), AdaptiveProtocolModel()
        result = agent.investigate(source, model_call=model)
        assert result["status"] == "completed"
        assert source.calls == expected
        assert source.auth_count >= len(expected) + 1
        assert result["result"]["facts"][0]["text"] == request
        continuation = model.inputs[1]["messages"]
        assert continuation[-1]["tool_call_id"] == continuation[-2]["tool_calls"][0]["id"]
        assert "PRIVATE CHAIN" not in json.dumps(result)
        assert "PRIVATE CHAIN" not in json.dumps(continuation)


def test_first_tool_is_model_choice_and_progress_accumulates(agent):
    source = Source()
    model = Script(response(calls=[call("get_request_trace", {"case_id": "case-1"})]), response(answer("502 upstream unavailable", ["request:1"])))
    updates = []
    result = agent.investigate(source, model_call=model, progress=updates.append)
    assert result["status"] == "completed"
    assert source.calls == ["get_request_trace"]
    assert updates[-1] == result
    required = {"status", "stop_reason", "result", "evidence", "trace", "rounds", "usage", "elapsed_ms", "model"}
    assert all(required <= set(u) for u in updates)
    tool_states = [t["status"] for u in updates for t in u["trace"] if t["kind"] == "tool"]
    assert "running" in tool_states and "completed" in tool_states
    assert any(u["evidence"] and u["status"] == "running" for u in updates)


@pytest.mark.parametrize("tool", [call("delete_case"), call(args={"run_id": "another"}), call("get_request_trace", {"case_id": "case-1", "source": "ui"}), call("get_request_trace", {"case_id": ["case-1"]})])
def test_forged_tools_and_scope_arguments_never_dispatch(agent, tool):
    source = Source()
    result = agent.investigate(source, model_call=Script(response(calls=[tool])))
    assert result["status"] == "limited"
    assert result["stop_reason"] in {"invalid_tool", "invalid_tool_arguments"}
    assert source.calls == []


@pytest.mark.parametrize("content, reason", [(answer(refs=["forged"]), "invalid_evidence_references"), (dict(answer(), facts=[{"text": "database definitely destroyed", "evidence_ids": []}]), "invalid_evidence_references"), ("not JSON secret", "invalid_final_output")])
def test_invalid_final_preserves_collected_evidence_without_success(agent, content, reason):
    result = agent.investigate(Source(), model_call=Script(response(calls=[call()]), response(content)))
    assert result["status"] == "limited"
    assert result["stop_reason"] == reason
    assert result["result"]["facts"] == []
    assert result["evidence"][0]["id"] == "failure:1"
    assert "not JSON secret" not in json.dumps(result)


def test_empty_evidence_is_not_confirmed_success(agent):
    result = agent.investigate(Source(), model_call=Script(response({"summary": "done", "facts": [], "hypotheses": [], "recommendations": [], "missing_evidence": []})))
    assert result["status"] == "limited"
    assert result["stop_reason"] == "no_evidence"


def test_cancellation_between_model_and_tool_never_dispatches(agent):
    source, stopped = Source(), [False]
    def model(**kwargs):
        stopped[0] = True
        return response(calls=[call()])
    result = agent.investigate(source, model_call=model, cancelled=lambda: stopped[0])
    assert result["status"] == "cancelled"
    assert source.calls == []


def test_cancellation_preserves_prior_evidence(agent):
    source = Source()
    def progress(update):
        if update["evidence"]:
            stopped[0] = True
    stopped = [False]
    result = agent.investigate(source, model_call=Script(response(calls=[call()])), progress=progress, cancelled=lambda: stopped[0])
    assert result["status"] == "cancelled"
    assert len(result["evidence"]) == 1


def test_permission_is_rechecked_before_each_call(agent):
    source = Source()
    def model(**kwargs):
        source.revoked = True
        return response(calls=[call()])
    result = agent.investigate(source, model_call=model)
    assert result["status"] == "failed"
    assert result["stop_reason"] == "access_denied"
    assert source.calls == []
    assert "sk-secret" not in json.dumps(result)


def test_repeated_call_stops_after_two_actual_reads(agent):
    source = Source()
    model = Script(*[response(calls=[call(ident=f"call_{i}")]) for i in range(3)])
    result = agent.investigate(source, model_call=model)
    assert result["status"] == "limited"
    assert result["stop_reason"] == "repeated_tool_limit"
    assert len(source.calls) == 2


def test_duplicate_tool_call_ids_rejected(agent):
    source = Source()
    result = agent.investigate(source, model_call=Script(response(calls=[call(), call()])))
    assert result["status"] == "limited"
    assert result["stop_reason"] == "invalid_tool_call_id"
    assert source.calls == []


def test_wall_budget_uses_remaining_timeout(agent, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(agent.time, "monotonic", lambda: now[0])
    source = Source()
    def model(**kwargs):
        assert kwargs["timeout_seconds"] <= 10
        now[0] += 11
        return response(calls=[call()])
    result = agent.investigate(source, model_call=model, timeout_seconds=10)
    assert result["stop_reason"] == "time_budget"
    assert source.calls == []


def test_round_limit_hard_capped(agent):
    result = agent.investigate(Source(), model_call=Script(response(calls=[call()])), max_rounds=1)
    assert result["stop_reason"] == "round_budget"
    assert result["rounds"] == 1


def test_reported_token_budget_stops_before_tools(agent):
    item = response(calls=[call()])
    item["usage"] = {"prompt_tokens": 18000, "completion_tokens": 1, "total_tokens": 18001}
    source = Source()
    result = agent.investigate(source, model_call=Script(item))
    assert result["stop_reason"] == "token_budget"
    assert source.calls == []


def test_missing_usage_is_honest_and_conservatively_budgeted(agent):
    result = agent.investigate(Source(), model_call=Script(response(calls=[call()], usage=False), response(answer(), usage=False)))
    assert result["usage"]["complete"] is False
    assert result["usage"]["total_tokens"] == 0
    assert result["usage"]["missing_usage_calls"] == 2
    assert result["usage"]["estimated_tokens"] > 0
    assert result["usage"]["accounted_tokens"] <= 18000


def test_no_sensitive_raw_errors_returned(agent):
    def failed(**kwargs):
        raise RuntimeError("Authorization Bearer sk-secret customer@example.com")
    result = agent.investigate(Source(), model_call=failed)
    assert result["status"] == "failed"
    assert result["stop_reason"] == "model_request_failed"
    assert "sk-secret" not in json.dumps(result)
    assert "customer@" not in json.dumps(result)


def test_large_tool_result_is_bounded_without_losing_prior_evidence(agent):
    source = Source()
    def large(case_id):
        return {"evidence": [{"id": "large:1", "title": "huge", "kind": "request", "href": "/report/1", "data": {"body": "x" * 200000}}], "missing": []}
    source.get_request_trace = large
    result = agent.investigate(source, model_call=Script(response(calls=[call()]), response(calls=[call("get_request_trace", {"case_id": "case-1"}, "large")])) )
    assert result["status"] == "limited"
    assert result["stop_reason"] == "evidence_budget"
    assert result["evidence"][0]["id"] == "failure:1"
    assert len(json.dumps(result)) < 100000


def test_model_cannot_supply_external_evidence_links(agent):
    content = answer()
    content["facts"][0]["href"] = "https://evil.test"
    result = agent.investigate(Source(), model_call=Script(response(calls=[call()]), response(content)))
    assert result["status"] == "limited"
    assert "evil.test" not in json.dumps(result)


def test_tool_limit_counts_all_calls_in_parallel_turn(agent):
    source = Source()
    def any_case(case_id):
        source.calls.append(case_id)
        return {'evidence': [], 'missing': []}
    source.get_request_trace = any_case
    model = Script(response(calls=[call('get_request_trace', {'case_id': str(i)}, f'c{i}') for i in range(12)]), response(calls=[call(ident='extra')]))
    result = agent.investigate(source, model_call=model)
    assert len(source.calls) == 12
    assert result['stop_reason'] == 'tool_budget'


def test_round_limit_cannot_be_expanded_by_caller(agent):
    source = Source()
    source.get_request_trace = lambda case_id: {'evidence': [], 'missing': []}
    model = Script(*[response(calls=[call('get_request_trace', {'case_id': str(i)}, f'c{i}')]) for i in range(8)])
    result = agent.investigate(source, model_call=model, max_rounds=1000)
    assert result['rounds'] == 6
    assert len(model.inputs) == 6
    assert result['stop_reason'] == 'round_budget'


def test_protocol_matches_multiple_tool_results_to_unique_ids(agent):
    model = Script(response(calls=[call(ident='failed'), call('get_request_trace', {'case_id': 'case-1'}, 'request')]), response(answer()))
    result = agent.investigate(Source(), model_call=model)
    assert result['status'] == 'completed'
    messages = model.inputs[1]['messages']
    replies = [m for m in messages if m['role'] == 'tool']
    assert [r['tool_call_id'] for r in replies] == ['failed', 'request']


def test_evidence_missing_is_preserved_even_if_model_omits_it(agent):
    source = Source()
    source.get_request_trace = lambda case_id: {'evidence': [], 'missing': ['本次运行没有网络证据']}
    model = Script(response(calls=[call(), call('get_request_trace', {'case_id': 'case-1'}, 'network')]), response(answer()))
    result = agent.investigate(source, model_call=model)
    assert result['result']['missing_evidence'] == ['本次运行没有网络证据']


def test_forged_safe_error_object_cannot_expose_raw_error(agent):
    def model(**kwargs):
        raise agent.transport.ModelError('sk-secret at customer@example.com')
    result = agent.investigate(Source(), model_call=model)
    assert result['stop_reason'] == 'model_request_failed'
    assert 'sk-secret' not in json.dumps(result)


def test_http_200_empty_message_is_limited_not_success(agent):
    result = agent.investigate(Source(), model_call=Script({'choices': []}))
    assert result['status'] == 'limited'
    assert result['stop_reason'] == 'invalid_model_response'


def test_tool_failure_never_discards_prior_read_or_exposes_details(agent):
    source = Source()
    def fail(case_id):
        raise ValueError('Bearer private-token')
    source.get_request_trace = fail
    result = agent.investigate(source, model_call=Script(response(calls=[call()]), response(calls=[call('get_request_trace', {'case_id': 'case-1'}, 'request')])))
    assert result['status'] == 'failed'
    assert len(result['evidence']) == 1
    assert result['trace'][-1]['status'] == 'failed'
    assert 'private-token' not in json.dumps(result)


def test_low_timeout_and_pre_cancel_make_no_model_request(agent):
    for options, reason in [({'timeout_seconds': 0}, 'time_budget'), ({'cancelled': lambda: True}, 'cancelled')]:
        model = Script()
        result = agent.investigate(Source(), model_call=model, **options)
        assert result['stop_reason'] == reason
        assert not model.inputs


def test_acceptance_cli_entrypoint_exists():
    assert importlib.util.find_spec('tools.failure_agent_acceptance') is not None


def test_acceptance_cli_private_output_and_compact_summary(tmp_path, monkeypatch, capsys):
    cli = importlib.import_module('tools.failure_agent_acceptance')
    outcome = {'mode': 'real_report', 'analysis': {'status': 'completed', 'stop_reason': 'model_completed', 'rounds': 2,
        'trace': [{'kind': 'tool', 'name': 'get_failed_steps'}], 'result': {'summary': '具体失败待核对'},
        'usage': {'total_tokens': 120}, 'evidence': [{'data': {'private': 'RAW_EVIDENCE_ONLY_IN_PRIVATE_FILE'}}]}}
    monkeypatch.setattr(cli, 'run_real', lambda *args, **kwargs: outcome)
    target = tmp_path / 'analysis.json'
    assert cli.main(['--source', 'api', '--run-id', 'run-1', '--username', 'private-admin', '--output', str(target)]) == 0
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text())['analysis']['usage']['total_tokens'] == 120
    printed = capsys.readouterr().out
    assert 'get_failed_steps' in printed
    assert 'RAW_EVIDENCE' not in printed
    assert 'private-admin' not in printed


def test_acceptance_cli_refuses_existing_output_before_model(tmp_path, monkeypatch, capsys):
    cli = importlib.import_module('tools.failure_agent_acceptance')
    target = tmp_path / 'existing.json'
    target.write_text('keep')
    def forbidden(*args, **kwargs):
        pytest.fail('existing output must be checked before any model call')
    monkeypatch.setattr(cli, 'run_real', forbidden)
    assert cli.main(['--source', 'api', '--run-id', 'run-1', '--username', 'admin', '--output', str(target)]) == 3
    assert target.read_text() == 'keep'
    assert 'output_unavailable' in capsys.readouterr().out


def test_acceptance_cli_errors_are_sanitized_in_file_and_console(tmp_path, monkeypatch, capsys):
    cli = importlib.import_module('tools.failure_agent_acceptance')
    def denied(*args, **kwargs):
        raise PermissionError('sk-secret private-admin')
    monkeypatch.setattr(cli, 'run_real', denied)
    target = tmp_path / 'failed.json'
    assert cli.main(['--source', 'ui', '--run-id', 'run-1', '--username', 'admin', '--output', str(target)]) == 3
    all_output = target.read_text() + capsys.readouterr().out
    assert 'access_denied' in all_output
    assert 'sk-secret' not in all_output
    assert 'private-admin' not in all_output


def test_acceptance_synthetic_scenarios_are_explicit_and_report_actual_paths(monkeypatch, tmp_path, capsys):
    cli = importlib.import_module('tools.failure_agent_acceptance')
    actor = {'kind': 'user', 'username': 'admin', 'user_id': 'one', 'display_name': 'Admin'}
    monkeypatch.setattr(cli, '_actor', lambda _username: actor)
    from task_server.services import failure_analysis_agent as loop
    seen = []
    def model(**kwargs):
        tool_messages = [json.loads(m['content']) for m in kwargs['messages'] if m['role'] == 'tool']
        if not tool_messages:
            return response(calls=[call()])
        if len(tool_messages) == 1:
            return response(calls=[call('get_request_trace', {'case_id': 'representative'}, 'network')])
        trace = tool_messages[1]['evidence'][0]
        seen.append(trace['data']['synthetic'])
        if trace['data']['response']['status_code'] == 200 and len(tool_messages) == 2:
            return response(calls=[call('compare_last_pass', {'case_id': 'representative'}, 'compare')])
        return response(answer('fixture observation', [trace['id']]))
    monkeypatch.setattr(loop.transport, 'call_model', model)
    target = tmp_path / 'scenarios.json'
    status = cli.main(['--protocol-scenarios', '--username', 'admin', '--output', str(target)])
    assert status == 0
    artifact = json.loads(target.read_text())
    assert artifact['mode'] == 'synthetic_evidence_real_model'
    assert artifact['different_tool_paths'] is True
    assert len(artifact['scenarios']) == 2
    assert all(seen)
    assert 'synthetic_evidence_real_model' in capsys.readouterr().out


def test_twenty_five_chinese_failures_still_reach_model_for_analysis(agent):
    source = Source()
    message = '页面加载完成等待超时，实际仅显示加载中，未获得请求证据，需继续区分服务异常与展示异常。' * 4
    failures = {'evidence': [{'id': f'failure:{i}', 'kind': 'failed_steps', 'title': f'模型列表用例{i}',
        'href': '/report/1', 'data': {'case_id': f'case-{i}', 'error_message': message, 'status': 'FAILED'}} for i in range(25)],
        'cases': [{'case_id': f'case-{i}', 'name': f'模型列表用例{i}', 'status': 'FAILED'} for i in range(25)], 'missing': []}
    assert 18000 < len(json.dumps(failures, ensure_ascii=False).encode()) < 32000
    source.get_failed_steps = lambda: failures
    first = response(calls=[call()])
    first['usage'] = {'prompt_tokens': 500, 'completion_tokens': 40, 'total_tokens': 540}
    model = Script(first, response(answer('25项观察仅为同表象，根因待核实', ['failure:0'])))
    result = agent.investigate(source, model_call=model)
    assert result['status'] == 'completed'
    assert len(model.inputs) == 2
    assert len(result['evidence']) == 25
    assert result['result']['summary']
    assert len(json.loads(model.inputs[1]['messages'][-1]['content'])['evidence']) == 25
    assert result['usage']['estimate_method'] == 'weighted_characters'
