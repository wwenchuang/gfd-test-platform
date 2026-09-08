import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('load_demo',Path(__file__).resolve().parents[2]/'deploy/load-demo/server.py')
demo=importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

def test_real_cgroup_metrics_and_no_synthetic_fallback(tmp_path):
    with pytest.raises((OSError,ValueError)):demo.metrics(tmp_path)
    for name,text in {'cpu.stat':'usage_usec 1500000\n','cpu.max':'50000 100000\n','memory.current':'104857600','memory.stat':'inactive_file 20971520\n','memory.max':'268435456'}.items():(tmp_path/name).write_text(text)
    data=demo.metrics(tmp_path)
    assert ' 1.5\n' in data and ' 83886080\n' in data and ' 50000\n' in data
    assert 'id="/load-demo"' in data
    (tmp_path/'cpu.max').write_text('max 100000')
    assert 'container_spec_cpu_quota' not in demo.metrics(tmp_path)

import threading,json,urllib.request,urllib.error,time
@pytest.fixture
def running(tmp_path):
    tokens={k:k*40 for k in ('api','metrics','admin')}
    server=demo.Server(('127.0.0.1',0),tokens,tmp_path)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    def call(path,kind='api',method='GET'):
        req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,headers={'Authorization':'Bearer '+tokens.get(kind,'invalid')},method=method)
        try:r=urllib.request.urlopen(req,timeout=3)
        except urllib.error.HTTPError as e:r=e
        return r.status,r.read()
    yield server,call
    server.shutdown();server.server_close();t.join()

def test_auth_status_and_business_failures(running):
    server,call=running
    assert call('/demo/ok','invalid')[0]==401
    assert call('/demo/ok')[0]==200
    assert call('/demo/http-error')[0]==500
    status,body=call('/demo/business-error')
    assert status==200 and json.loads(body)['code']==1001
    assert call('/demo/cpu?seconds=999')[0]==400
    assert call('/admin/reset','api','POST')[0]==401
    assert call('/metrics')[0]==401
    assert call('/metrics','metrics')[0]==503

def test_shared_bounded_memory_reset_and_expiry(running):
    server,call=running
    call('/demo/memory');original=server.state.memory
    expiry=server.state.memory_until
    call('/demo/memory');assert server.state.memory is original
    assert server.state.memory_until==expiry and len(original)==64*1024*1024
    call('/admin/reset','admin','POST');assert server.state.memory is None
    call('/demo/memory');server.state.memory_until=time.monotonic()-.1
    time.sleep(.7);assert server.state.memory is None

def test_concurrency_backpressure_and_bounded_cpu(running):
    server,call=running
    for _ in range(4):server.state.work.acquire()
    assert call('/demo/ok')[0]==429
    # Monitoring is not starved by workload slots.
    assert call('/health')[0]==200
    for _ in range(4):server.state.work.release()
    start=time.monotonic();assert call('/demo/cpu')[0]==200
    assert time.monotonic()-start<2

def test_pause_is_separate_admin_and_resets(running):
    server,call=running
    assert call('/admin/pause-metrics','admin','POST')[0]==200
    assert 119<server.state.pause_until-time.monotonic()<=120
    assert call('/demo/ok')[0]==200
    call('/admin/reset','admin','POST');assert server.state.pause_until==0

def test_runtime_fault_starts_healthy_and_admin_reset_restores(running):
    server,call=running
    assert call('/demo/switchable')[0]==200
    assert call('/admin/http-error','api','POST')[0]==401
    assert call('/admin/http-error','admin','POST')[0]==200
    assert call('/demo/switchable')[0]==500
    assert call('/demo/ok')[0]==200
    call('/admin/business-error','admin','POST')
    status,body=call('/demo/switchable')
    assert status==200 and json.loads(body)['code']==1001
    server.state.fault=('http-error',time.monotonic()-1)
    assert call('/demo/switchable')[0]==200
    call('/admin/http-error','admin','POST')
    call('/admin/reset','admin','POST')
    assert json.loads(call('/demo/switchable')[1])['code']==0
