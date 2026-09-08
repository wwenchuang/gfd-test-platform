"""Opt-in REAL local Docker/Prometheus check, no business/AI traffic.
Run from repo using .venv/bin/python tests/load_demo/accept_local.py.
"""
import json,time,urllib.request,urllib.parse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from task_server.api_testing.services.load_monitoring_collection_service import LoadMonitoringCollectionService
ROOT=Path(__file__).resolve().parents[2]/'deploy/load-demo'

def call(path,kind='api',method='GET'):
    token=(ROOT/'secrets'/(kind+'_token')).read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:18080'+path,headers={'Authorization':'Bearer '+token},method=method)
    with urllib.request.urlopen(req,timeout=4) as response:return response.read().decode()

class LocalPrometheus:
    def query_range(self,query,start,end,step):
        # Local test adapter only. Production client keeps SSRF/DNS restrictions.
        url='http://127.0.0.1:19090/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'start':start,'end':end,'step':step})
        with urllib.request.urlopen(url,timeout=10) as response:body=json.load(response)
        assert body['status']=='success',body
        return [{'labels':r['metric'],'points':[{'timestamp':float(t),'value':float(v)} for t,v in r['values']]} for r in body['data']['result']]

source=LocalPrometheus()
svc=LoadMonitoringCollectionService(None,monitoring_service=None)
definition={'name':'本地独立演示容器','deployment':'container','labels':{'instance':'demo:8080','id':'/load-demo'},'step_seconds':5}
start=int(time.time())
call('/demo/memory')
for _ in range(20):
    call('/demo/cpu')
    time.sleep(.5)
time.sleep(6)
end=int(time.time())-5
metrics=[svc._metric(source,definition,key,start,end) for key in ('cpu_cores','memory_working_set_bytes')]
for m in metrics:
    values=[p for s in m['series'] for p in s['points'] if p['value'] is not None]
    assert values, f"{m['key']}: no real data; allow two minutes of scrape history"
    assert all(p['utilization_percent'] is not None for p in values), 'missing quota'
    print(m['key'],'peak',m['peak'],'quota',values[-1]['denominator_value'],'utilization',values[-1]['utilization_percent'])
assert metrics[1]['peak']>64*1024*1024
Path('/tmp/load-demo-real-metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2))
try:
    call('/admin/pause-metrics','admin','POST')
    time.sleep(12)
    end=int(time.time())
    missing=source.query_range('up{job="load-demo-self-cgroup"}',end-5,end,5)
    assert missing and missing[0]['points'][-1]['value']==0
    print('PASS: real scrape failure detected, workload remains available',call('/demo/ok')[:30])
finally:call('/admin/reset','admin','POST')
print('PASS: actual container metrics queried with platform PromQL; no business requests')
