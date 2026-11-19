"""Read a QA print job through the service user's named MySQL login."""
import json
import os
from pathlib import Path
import re
import subprocess


def read_events(machine, job):
    # The MySQL CLI receives SQL on stdin. Only canonical identifiers may enter it.
    if not re.fullmatch(r'[0-9A-F]{12}', machine) or not re.fullmatch(r'[0-9]{1,20}', job):
        raise ValueError('invalid_print_reference')
    colon_mac = ":".join(machine[i:i + 2] for i in range(0, 12, 2))
    sql = f"""
SET SESSION MAX_EXECUTION_TIME=3000;
START TRANSACTION READ ONLY;
SET @mac='{machine}', @job='{job}';
SELECT j.created_at,j.finished_at,j.status INTO @start,@end,@status
FROM print3d.print_job j JOIN print3d.devices d ON d.sn=j.device_sn
WHERE j.sn=@job AND d.mac IN (@mac,'{colon_mac}');
SELECT JSON_OBJECT('source','print3d.print_job','id',@job,'time',COALESCE(@end,@start),'created_at',@start,'finished_at',@end,'stage',@status,'match','任务号与设备')
FROM DUAL WHERE @start IS NOT NULL;
SELECT JSON_OBJECT('source','print3d_gcp.iot_downs','id',id,'time',created_at,'stage',cmd_code,'match','设备与请求号')
FROM print3d_gcp.iot_downs WHERE @start IS NOT NULL AND device_id=@mac AND request_id=@job
AND cmd_code='print' ORDER BY id LIMIT 10;
SELECT JSON_OBJECT('source','print3d_gcp.iot_ups','id',id,'time',created_at,
'stage',CONCAT(content->>'$.operation','/',content->>'$.status'),'match','设备与请求号')
FROM print3d_gcp.iot_ups WHERE @start IS NOT NULL AND device_id=@mac AND request_id=@job
AND created_at BETWEEN @start AND LEAST(COALESCE(@end,NOW()),@start+INTERVAL 24 HOUR)
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.operation')),NULL) IN ('download','print')
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.status')),NULL) IN ('started','completed','failed')
ORDER BY created_at,id LIMIT 50;
SELECT JSON_OBJECT('source','print3d_gcp.iot_ups','id',id,'time',created_at,
'stage','PowerLoss','match','同设备、结束前后两分钟；物理原因待核实')
FROM print3d_gcp.iot_ups WHERE device_id=@mac AND created_at BETWEEN
GREATEST(@start,@end-INTERVAL 2 MINUTE) AND @end+INTERVAL 2 MINUTE
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.print_state')),NULL)='PowerLoss'
ORDER BY created_at,id LIMIT 1;
ROLLBACK;
"""
    output = subprocess.run(
        ['mysql', '--defaults-file=' + '/opt/midscene-auth/print-patrol-qa.cnf', '--ssl-mode=REQUIRED',
         '--connect-timeout=4', '--batch', '--skip-column-names'],
        env=dict(os.environ, MYSQL_TEST_LOGIN_FILE="/dev/null"), input=sql, text=True, capture_output=True, timeout=18, check=True)
    return [json.loads(line) for line in output.stdout.splitlines() if line]


class PrintInvestigation:
    """Standalone print failure, with an operator-bound owner and device."""
    def __init__(self, actor, job):
        self.actor = actor
        self.job = job

    def authorize(self):
        from .failure_analysis_tasks import _profile
        _profile(self.actor)
        config = Path('/opt/midscene-auth/print-investigations.json')
        if not config.exists():
            raise LookupError('print_job_unavailable')
        entry = json.loads(config.read_text()).get(self.job, {})
        if self.actor['username'] not in entry.get('readers', []):
            raise PermissionError('print_job_unavailable')
        return entry['machine']

    def get_failed_steps(self):
        self.authorize()
        return {'cases': [{'case_id': self.job, 'name': '测试库打印失败任务，请查设备上下行回执并区分任务号与时间关联'}],
                'evidence': [], 'missing': []}

    def get_device_logs(self, case_id):
        machine = self.authorize()
        if case_id != self.job:
            raise LookupError('case_not_found')
        rows = read_events(machine, self.job)
        self.authorize()
        return {'evidence': [dict(id=f"{r['source']}:{r['id']}", kind='device_logs',
                                  title=r['stage'], href=None,
                                  data=dict(r, device=machine, job=self.job)) for r in rows],
                'missing': [] if rows else ['设备与业务任务未匹配。']}

    def compare_last_pass(self, case_id):
        self.authorize()
        return {'evidence': [], 'missing': ['没有已授权的同条件成功打印任务。']}

    def get_request_trace(self, case_id):
        self.authorize()
        return {'evidence': [], 'missing': ['本报告是独立打印任务，请查询 get_device_logs。']}

    def qualify_result(self, payload):
        """Anchor observations to typed events; keep causal explanations hypothetical."""
        if payload['status'] != 'completed':
            return
        labels = {'print': '记录了打印指令下发', 'download/started': '设备回报开始下载',
                  'download/completed': '设备回报下载完成', 'print/started': '设备回报开始打印',
                  'PowerLoss': '同一设备在任务结束前后两分钟内上报 PowerLoss，按设备与时间关联',
                  'printFail': '业务任务最终标记为 printFail'}
        facts = []
        for item in payload['evidence']:
            row = item['data']
            if row['stage'] in labels:
                facts.append({'text': f"{row['time']}，{labels[row['stage']]}。",
                              'evidence_ids': [item['id']]})
        result = payload['result']
        result['facts'] = facts
        result['summary'] = ('已核对下列任务记录和设备回执。设备上报的状态可用于定位异常时段，'
                             '具体物理原因仍待核实。' if facts else '缺少可核对的任务或设备记录。')
