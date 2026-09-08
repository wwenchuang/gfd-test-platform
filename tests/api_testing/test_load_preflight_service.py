"""Single-iteration safety checks before distributed load begins."""

from types import SimpleNamespace

from task_server.api_testing.services.load_preflight_service import LoadPreflightService


def _definition():
    def step(identifier, scope):
        return {
            "id": identifier,
            "name": identifier,
            "scope": scope,
            "action": "http_request",
            "request": {
                "method": "GET",
                "path": "/" + identifier,
                "service": "default",
                "path_params": {},
                "query": {},
                "headers": {},
                "cookies": {},
                "body": None,
            },
            "assertions": [],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "cleanup_owned_resource" if scope == "cleanup_once" else "readonly",
        }

    definition = {
        "name": "搜索核心链路",
        "description": "预检只执行一轮",
        "mode": "workflow",
        "steps": [
            step("agent-setup", "agent_setup"),
            step("vu-setup", "vu_once"),
            step("main", "iteration"),
            step("cleanup", "cleanup_once"),
        ],
        "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
        "risk": {"level": "low", "ownership_variable": None, "notes": ""},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }

    definition['steps'][2].update(side_effect='creates_owned_resource',cleanup_step_id='cleanup',extractions=[{'target':'main_id','type':'json_path','path':'$.id','required':True}])
    definition['steps'][2]['request']['method']='POST'
    definition['steps'][3]['request'].update(method='DELETE',path='/resources/{{main_id}}')
    definition['risk']['ownership_variable']='main_id'
    return definition


class _StepRunner:
    def __init__(self, failed_step=None):
        self.calls = []
        self.variables = []
        self.failed_step = failed_step

    def execute(self, step, environment_revision_id, variables):
        self.calls.append(step["id"])
        self.variables.append(dict(variables))
        status = "FAILED" if step["id"] == self.failed_step else "PASSED"
        return {
            "status": status,
            "duration_ms": 25,
            "status_code": 200,
            "failure_category": "product_assertion" if status == "FAILED" else "",
            "error_message": "业务断言失败" if status == "FAILED" else "",
            "extracted_variables": {step["id"] + "_id": "owned-1"},
            "assertions": [],
        }


def _agents():
    return [
        SimpleNamespace(id="agent-a", name="上海专用节点"),
        SimpleNamespace(id="agent-b", name="北京专用节点"),
    ]


def test_preflight_executes_every_scope_once_and_checks_each_agent_connectivity():
    runner = _StepRunner()
    probes = []

    def probe(agent, revision_id):
        probes.append((agent.id, revision_id))
        return {"reachable": True, "dns_ms": 2, "connect_ms": 8, "tls_ms": 12}

    result = LoadPreflightService(runner, connectivity_probe=probe).run_once(
        _definition(), "environment-v3", _agents()
    )

    assert result.passed is True
    assert runner.calls == ["agent-setup", "vu-setup", "main", "cleanup"]
    assert probes == [("agent-a", "environment-v3"), ("agent-b", "environment-v3")]
    assert result.iteration_count == 1
    assert result.observed_duration_ms == 100
    assert result.cleanup_status == "passed"
    assert result.estimated_vus_for_rate(40) == 4
    assert result.to_dict()["steps"][0]["extracted_variable_names"] == ["agent-setup_id"]
    assert "extracted_variables" not in result.to_dict()["steps"][0]


def test_preflight_always_attempts_cleanup_after_main_failure():
    runner = _StepRunner(failed_step="main")

    result = LoadPreflightService(
        runner,
        connectivity_probe=lambda _agent, _revision: {"reachable": True},
    ).run_once(_definition(), "environment-v3", _agents()[:1])

    assert result.passed is False
    assert result.failure_code == "functional_preflight_failed"
    assert runner.calls[-1] == "cleanup"
    assert runner.variables[-1]["main_id"] == "owned-1"
    assert result.cleanup_status == "passed"


def test_unreachable_target_from_any_selected_agent_is_a_hard_block():
    runner = _StepRunner()

    def probe(agent, _revision):
        if agent.id == "agent-b":
            return {"reachable": False, "stage": "tls", "message": "证书校验失败"}
        return {"reachable": True, "connect_ms": 4}

    result = LoadPreflightService(runner, connectivity_probe=probe).run_once(
        _definition(), "environment-v3", _agents()
    )

    assert result.passed is False
    assert result.failure_code == "agent_target_unreachable"
    assert result.connectivity[1]["agent_name"] == "北京专用节点"
    assert result.connectivity[1]["message"] == "证书校验失败"


def test_preflight_creation_timeout_does_not_retry_or_delete_unknown_resource():
    class TimeoutRunner(_StepRunner):
        def execute(self, step, environment_revision_id, variables):
            if step['id']=='main':
                self.calls.append(step['id'])
                raise TimeoutError('unknown creation outcome')
            return super().execute(step,environment_revision_id,variables)
    runner=TimeoutRunner()
    result=LoadPreflightService(runner,connectivity_probe=lambda *_:{'reachable':True}).run_once(_definition(),'env',_agents()[:1])
    assert runner.calls==['agent-setup','vu-setup','main']
    assert not result.passed
    assert result.cleanup_status=='unknown_ownership'


def test_preflight_rejects_unsupported_setup_without_business_requests():
    definition=_definition();definition['steps'][0]['scope']='setup_once'
    runner=_StepRunner()
    result=LoadPreflightService(runner,connectivity_probe=lambda *_:{'reachable':True}).run_once(definition,'env',_agents()[:1])
    assert not result.passed and result.failure_code=='unsupported_lifecycle'
    assert not runner.calls


def test_preflight_write_adapter_uses_private_bounded_limits_without_redirects():
    from task_server.api_testing.services.load_preflight_service import FunctionalLoadStepRunner
    from task_server.api_testing.executor import ExecutorLimits
    class Executor:
        limits=ExecutorLimits()
        seen=[]
        def _execute_http_step(self,*args,**kwargs):
            self.seen.append(self.limits)
            return SimpleNamespace(status='PASSED',response={'status_code':200},failure_category='',error='',secrets=(),extracted={'main_id':'owned'},assertions=[])
    executor=Executor()
    FunctionalLoadStepRunner(executor).execute(_definition()['steps'][2],'env',{})
    assert executor.seen[0].max_redirects==0
    assert executor.seen[0].timeout_seconds==10
    assert executor.limits.max_redirects==5



def test_preflight_rejects_url_delimiters_and_encoded_ids_before_delete():
    from task_server.api_testing.services.load_lifecycle_policy import valid_owned_id
    for resource_id in ('historical?x=1', 'historical#new', 'historical%3Fx=1', '%252e%252e', 'a.b', 'white space'):
        class UnsafeIdRunner(_StepRunner):
            def execute(self, step, environment_revision_id, variables):
                outcome=super().execute(step, environment_revision_id, variables)
                if step['id']=='main': outcome['extracted_variables']={'main_id':resource_id}
                return outcome
        runner=UnsafeIdRunner()
        result=LoadPreflightService(runner,connectivity_probe=lambda *_:{'reachable':True}).run_once(_definition(),'env',_agents()[:1])
        assert not valid_owned_id(resource_id)
        assert 'cleanup' not in runner.calls
        assert result.cleanup_status=='unknown_ownership'


def test_created_owner_survives_other_required_extraction_failure_without_repeating_http():
    from task_server.api_testing.services.load_preflight_service import FunctionalLoadStepRunner
    from task_server.api_testing.executor import ExecutorLimits
    class Executor:
        limits=ExecutorLimits()
        calls=[]
        def _execute_http_step(self,request,assertions,extractions,*args,**kwargs):
            self.calls.append(request['method'])
            return SimpleNamespace(status='BROKEN' if extractions else 'PASSED',response={'status_code':200},failure_category='',error='',secrets=(),extracted={},assertions=[],raw_response=None if extractions else {'status_code':200,'body':{'id':42},'headers':{},'cookies':{}})
    executor=Executor()
    step=_definition()['steps'][2]
    step['extractions'].append({'target':'missing','type':'json_path','path':'$.absent','required':True})
    result=FunctionalLoadStepRunner(executor).execute(step,'env',{})
    assert executor.calls==['POST']
    assert result['status']=='BROKEN'
    assert result['extracted_variables']['main_id']==42


def test_preflight_numeric_id_is_canonical_string_for_placeholder_resolver():
    class NumericRunner(_StepRunner):
        def execute(self,step,env,variables):
            result=super().execute(step,env,variables)
            if step['id']=='main': result['extracted_variables']['main_id']=42.0
            return result
    runner=NumericRunner()
    result=LoadPreflightService(runner,connectivity_probe=lambda *_:{'reachable':True}).run_once(_definition(),'env',_agents()[:1])
    assert result.passed
    assert runner.variables[-1]['main_id']=='42'
