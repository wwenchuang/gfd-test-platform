"""Build the scoped publication from completed raw measurements, without JMeter logs or credentials."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED
from datetime import datetime, timezone, timedelta
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/perf_report'))
from report import build_report

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('run',type=Path)
    ap.add_argument('environment',type=Path)
    ap.add_argument('output',type=Path)
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    doc=build_report(a.run)
    if doc['missing_stages'] or doc['pending_stages'] or len(doc['runs'])!=4 or not all(
            x['status']=='passed' and x['complete_steady_window'] for x in doc['runs']):
        raise ValueError('not a completed staircase: retain actual partial evidence separately')
    if not all(x['expected_outcome_observed'] for x in doc['checks']):raise ValueError('prechecks failed')
    def encode(data):return json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    def at(stamp):return datetime.fromtimestamp(stamp/1000,timezone(timedelta(hours=8)))
    env=json.loads(a.environment.read_bytes())
    if env['account_role']!='viewer' or env['route']!='/api/platform/requirement-design/knowledge/evaluation':raise ValueError('wrong target')
    def display_monitor(row):
        item=dict(row)
        labels={'cpu_percent':'进程 CPU','rss_kib':'驻留内存','db_bytes':'数据库文件','wal_bytes':'WAL 日志'}
        item['metric']=labels.get(item['metric'],item['metric'])
        scale=1024 if row['unit']=='KiB' else 1
        if scale!=1:item['unit']='MiB'
        for key in ('mean','min','max'):item[key]=round(item[key]/scale,2)
        return item
    sts=[]
    for r in doc['runs']:
        s=r['steady']
        if s['duration_s']!=180 or r['warm']['duration_s']!=60:raise ValueError('incomplete window')
        sts.append(dict(phase='retest' if r['id']=='retest' else 'baseline',threads=r['concurrency'],ramp_s=0,warmup_s=60,
                        steady_s=s['duration_s'],elapsed_s=round((r['stage_end_ms']-r['stage_start_ms'])/1000,3),
                        **{k:s[k] for k in ('samples','errors','error_counts','http_successes','business_successes','p50_ms','p95_ms','p99_ms')},
                        tps=s['throughput_rps'],jtl_sha256=r['sha256']['requests.jtl'],monitor=[display_monitor(i) for i in r['monitor']['steady']]))
    comp=doc['comparisons'][0];assert comp['comparable']
    desc=(f"同样的 1 并发、60 秒预热、180 秒稳态复测：P95 从 {sts[0]['p95_ms']} 到 {sts[-1]['p95_ms']} 毫秒，"
          f"吞吐从 {sts[0]['tps']:.3f} 到 {sts[-1]['tps']:.3f} 次每秒，业务失败分别为 {sts[0]['errors']} 和 {sts[-1]['errors']} 次。")
    def peaks(source,metric):
        return [next(x['max'] for x in st['monitor']['steady'] if x['source']==source and x['metric']==metric) for st in doc['runs']]
    resources={}
    for source in ['generator','server']:
        resources[source]=(f"四档稳态按进程分别采样。最高 CPU {max(peaks(source,'cpu_percent')):.2f}%（单核为 100%），"
                           f"最高驻留内存 {max(peaks(source,'rss_kib'))/1024:.1f} MiB。")
    def idle_rss(run):
        return next(x['mean']/1024 for x in run['monitor']['recovery'] if x['source']=='server' and x['metric']=='rss_kib')
    first_idle,high_idle,last_idle=[idle_rss(doc['runs'][i]) for i in (0,2,3)]
    resources['server']+=(f" 20 秒恢复段的驻留内存均值：初次基线 {first_idle:.1f}、10 并发后 {high_idle:.1f}、"
                          f"最终复测后 {last_idle:.1f} MiB。短窗口不能区分分配器留存与持续增长。")
    resources['database']=(f"SQLite 嵌入服务进程。稳态数据库文件最大 {max(peaks('database','db_bytes'))/1024:g} KiB，"
                           f"WAL 文件最大 {max(peaks('database','wal_bytes'))/1024:g} KiB；未采集数据库锁等待或磁盘时延。")
    host_path=a.run/'host.csv'
    host_blob=None
    if host_path.is_file():
        host_blob=host_path.read_bytes()
        table=csv.DictReader(io.StringIO(host_blob.decode()))
        if table.fieldnames!=['timeStamp','cpu_busy_pct','free_pages_bytes','speculative_pages_bytes','active_pages_bytes','wired_pages_bytes']:
            raise ValueError('Unexpected host columns')
        host=[{k:float(v) for k,v in row.items()} for row in table]
        high=doc['runs'][2];selected=[r for r in host if high['planned_steady_start_ms']<=r['timeStamp']<high['planned_steady_end_ms']]
        if selected:
            resources['generator']+=f" 另从 5 并发途中开始采同机整机读数；10 并发稳态 {len(selected)} 次，整机 CPU 忙碌率均值 {sum(r['cpu_busy_pct'] for r in selected)/len(selected):.1f}%（全机为 100%），包含其他进程。"
    start=at(doc['checks'][0]['stage_start_ms'])
    end=at(doc['runs'][-1]['recovery_end_ms'])
    report=dict(schema_version=1,scope={'app_ids':['com.kfb.model']},created_at=end.isoformat(),environment='isolated',
                scenario='打开智小白 3D 的知识检索评估报告',conditions=(
                    f"北京时间 {start:%Y-%m-%d %H:%M:%S} 至 {end:%H:%M:%S}。平台版本 {env['revision'][:8]}，"
                    f"JMeter {env['jmeter']}、Java 17、{env['cpu_model']}，本机回环。同一只读账号通过 GET 读取相同的 30 题报告，"
                    "每线程间隔 1 秒，档间恢复至少 20 秒。先故意验 31 题，HTTP 200 被业务断言判失败；随后 1 用户 2 次均通过。"),
                thresholds={},stages=sts,resources=resources,retest=desc,
                stop_reason="所有档位和复测完成。保护条件：连续 3 次失败；最近 20 至 30 个样本错误率达到 5% 或 P95 超过 2 秒；服务消失、驻留内存超过 1536 MiB 或超时。",
                limitations=["仅覆盖打开已发布报告，不含知识检索、模型推理、登录耗时或反向代理；同机热点数据和 1 秒思考时间会影响吞吐。",
                             "未测饱和点。单轮小规模试验不能证明没有瓶颈，不能推算共享 QA 或生产容量。"])
    bundle=a.output/'jmeter-readonly-evidence.zip'
    with ZipFile(bundle,'w',compression=ZIP_DEFLATED,compresslevel=8) as z:
        for n in ['negative','smoke','baseline','medium','high','retest']:
            for f in ('requests.jtl','monitor.csv','metadata.json'):
                raw=(a.run/n/f).read_bytes()
                if f=='metadata.json':
                    keys=set(json.loads(raw))
                    allowed=set('stage threads think_ms warmup_seconds steady_seconds stage_start_ms stage_end_ms steady_start_ms steady_end_ms recovery_start_ms recovery_end_ms target_pid db_monitor status abort_reason exit_code samples'.split())
                    if keys-allowed:raise ValueError('Review new metadata fields: '+str(keys-allowed))
                elif f=='requests.jtl':
                    keys=next(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
                    safe='timeStamp elapsed label responseCode threadName success failureMessage bytes sentBytes grpThreads allThreads Latency IdleTime Connect'.split()
                    if keys!=safe:raise ValueError('Unexpected JTL columns')
                elif next(csv.reader(io.StringIO(raw.decode())))!=['timeStamp','source','metric','unit','value']:
                    raise ValueError('Unexpected resource fields')
                z.writestr('run/'+n+'/'+f,raw)
        if host_blob is not None:z.writestr('run/host.csv',host_blob)
        z.writestr('summary.json',encode(doc));z.writestr('environment.json',encode(env))
        z.writestr('README.txt','离线复算：python3 report.py run --output recomputed.json\nsummary.json 含各档预热、稳态、恢复的窗口和统计。原始 CSV 标签不含账号、请求头或正文。\nJMeter 原生稳态 HTML：在新的空目录生成，运行 python3 render_dashboards.py run dashboards --jmeter /路径/bin/jmeter --properties knowledge_jtl.properties。转换仅为离线报表补空的响应描述列，原始 JTL 不变。\n')
        for name,rel in [('test-plan.md','docs/audits/perf-knowledge/test-plan.md'),('report.py','tools/perf_report/report.py'),
                         ('knowledge_evaluation.jmx','tools/perf/knowledge_evaluation.jmx'),('knowledge_jtl.properties','tools/perf/knowledge_jtl.properties'),
                         ('run_knowledge_jmeter.py','tools/perf/run_knowledge_jmeter.py'),('render_dashboards.py','tools/perf_environment/render_dashboards.py')]:
            z.write(ROOT/rel,name)
    report['evidence']={'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'bytes':bundle.stat().st_size}
    (a.output/'jmeter-readonly.json').write_text(encode(report))
    (a.output/'summary.json').write_text(encode(doc))
    print('Evidence bundle:',report['evidence']['bytes'],'bytes;',sum(k['samples'] for k in sts),'steady samples')
if __name__=='__main__':main()
