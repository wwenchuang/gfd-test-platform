"""mitmdump addon. Retain only telemetry fields from the configured reporting URL."""
import json
import os
from urllib.parse import parse_qs, urlsplit

def mark(stage):
    path=os.getenv('TRACKING_DIAGNOSTICS')
    if path:
        with open(path,'a',encoding='utf8') as log:
            print(stage,file=log)

def tls_failed_client(data):
    if data.context.client.sni == urlsplit(os.environ['TRACKING_EVENT_URL']).hostname:
        mark('tls_failed_client')

def request(flow):
    target=urlsplit(os.environ['TRACKING_EVENT_URL'])
    url=urlsplit(flow.request.pretty_url)
    if (url.scheme,url.netloc,url.path)!=(target.scheme,target.netloc,target.path):return
    mark('matched_path')
    expected=parse_qs(target.query,keep_blank_values=True)
    expected.pop('type',None)  # One reporting path carries several transport groups.
    actual=parse_qs(url.query,keep_blank_values=True)
    if any(actual.get(key)!=values for key,values in expected.items()):return
    mark('matched_query')
    try:body=json.loads(flow.request.get_text(strict=True))
    except (ValueError,UnicodeError):return
    records=decode(body,flow.request.timestamp_start)
    for record in records: record['requestType']=actual.get('type',[''])[0][:32]
    flow.metadata['tracking_rows']=records

FIELDS=('page','pageTitle','ele','eleTitle','tab','topicId','topicName','element','elementId','modelName','modelType','resourceType',
        'modelId','firstExposurePosition','exposurePage','pagePre')

def decode(body, sent):
    batch=isinstance(body,dict) and 'evts' in body
    rows=body.get('evts') if batch else body if isinstance(body,list) else body.get('events',[body]) if isinstance(body,dict) else []
    result=[]
    if not isinstance(rows,list):return result
    for row in rows[:256]:
        if not isinstance(row,dict):continue
        kind=row.get('type' if batch else 'event')
        if not isinstance(kind,str) or not kind:continue
        contents=row.get('conts',[]) if batch else [row.get('properties',row)]
        if not isinstance(contents,list):contents=[]
        fields=[{k:str(item[k])[:160] for k in FIELDS if k in item and isinstance(item[k],(str,int,float))}
                for item in contents[:64] if isinstance(item,dict)]
        timestamp=row.get('time') if batch else None
        occurred=timestamp/1000 if type(timestamp) in (int,float) and 0<timestamp<10**14 else None
        result.append({'event':kind[:100],'name':str(row.get('eventName',''))[:100],
                       'time':occurred if batch else sent,'sent':sent,'contents':fields})
    return result

def save(flow,ack):
    rows=flow.metadata.pop('tracking_rows',[])
    if rows:
        with open(os.environ['TRACKING_ROWS'],'a',encoding='utf8') as f:
            for row in rows:
                print(json.dumps(dict(row,ack=ack)),file=f)

def response(flow):
    if not flow.metadata.get('tracking_rows'):return
    try:reply=json.loads(flow.response.get_text(strict=True))
    except (ValueError,UnicodeError):reply=None
    ack='回执未识别'
    if isinstance(reply,dict) and 'code' in reply:
        ack='已接收' if 200<=flow.response.status_code<300 and type(reply['code']) is int and reply['code']==0 else '上报失败'
    save(flow,ack)

def error(flow):
    save(flow,'未取得响应')
