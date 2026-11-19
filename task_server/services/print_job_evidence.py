"""Read a QA print job through the service user's named MySQL login."""
import json
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
WHERE @start IS NOT NULL;
SELECT JSON_OBJECT('source','print3d_gcp.iot_downs','id',id,'time',created_at,'stage',cmd_code,'match','设备与请求号')
FROM print3d_gcp.iot_downs WHERE @start IS NOT NULL AND device_id=@mac AND request_id=@job
AND cmd_code='print' ORDER BY id LIMIT 10;
SELECT JSON_OBJECT('source','print3d_gcp.iot_ups','id',id,'time',created_at,
'stage',CONCAT(content->>'$.operation','/',content->>'$.status'),'match','设备与请求号')
FROM print3d_gcp.iot_ups WHERE @start IS NOT NULL AND device_id=@mac AND request_id=@job
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.operation')),NULL) IN ('download','print')
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.status')),NULL) IN ('started','completed','failed')
ORDER BY id LIMIT 50;
SELECT JSON_OBJECT('source','print3d_gcp.iot_ups','id',id,'time',created_at,
'stage','PowerLoss','match','同设备、结束前后两分钟；物理原因待核实')
FROM print3d_gcp.iot_ups WHERE device_id=@mac AND created_at BETWEEN
GREATEST(@start,@end-INTERVAL 2 MINUTE) AND @end+INTERVAL 2 MINUTE
AND IF(JSON_VALID(content),JSON_UNQUOTE(JSON_EXTRACT(content,'$.print_state')),NULL)='PowerLoss'
ORDER BY created_at,id LIMIT 1;
ROLLBACK;
"""
    output = subprocess.run(
        ['mysql', '--no-defaults', '--login-path=print-patrol-qa', '--ssl-mode=REQUIRED',
         '--connect-timeout=4', '--batch', '--skip-column-names'],
        input=sql, text=True, capture_output=True, timeout=18, check=True)
    return [json.loads(line) for line in output.stdout.splitlines() if line]
