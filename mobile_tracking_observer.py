"""Extract a redacted request/response record at the configured telemetry endpoint."""
import hashlib
import json
import math
import os
import re
from urllib.parse import parse_qs, urlsplit

# Only these business fields can leave the observer. The enclosing device/user
# objects, authentication material and the other HAR entries stay local.
PUBLIC = frozenset("""
page pageTitle ele eleTitle tab subTab topicId element elementId modelType resourceType
modelId resourceId videoId firstExposurePosition exposurePage pagePre mediaType
functionUse existResult tabClick listClick rank duration playDuration watchDuration
channel position isTop clickArea favoriteType actDuration source
""".split())
HASHED = frozenset("""
keyword searchWord topicName modelName resourceName contentName videoName traceId
listRequestId requestId browseId batchId viewBatchId
""".split())
_EVENT = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,99}\Z')
_TEXT = re.compile(r'[\w\u4e00-\u9fff ·./:，、：-]{1,100}\Z')
_PRIVATE_TEXT = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)|https?://|(?:\d{1,3}\.){3}\d{1,3}|@')


def mark(stage):
    path=os.getenv('TRACKING_DIAGNOSTICS')
    if path:
        with open(path,'a',encoding='utf8') as log:
            print(stage,file=log)


def tls_failed_client(data):
    if data.context.client.sni == urlsplit(os.environ['TRACKING_EVENT_URL']).hostname:
        mark('tls_failed_client')


def object_value(value):
    if isinstance(value,str) and len(value)<65536 and value.lstrip().startswith('{'):
        try:value=json.loads(value)
        except ValueError:return {}
    return value if isinstance(value,dict) else {}


def redacted_fields(value):
    result={}
    for key,raw in object_value(value).items():
        if key not in PUBLIC|HASHED or type(raw) not in (str,bool,int,float):continue
        if type(raw) is float and not math.isfinite(raw):continue
        text=str(raw)
        if key in HASHED:
            result[key]='已脱敏·'+hashlib.sha256(text.encode()).hexdigest()[:12] if text else ''
        elif len(text)<=100 and _TEXT.fullmatch(text) and not _PRIVATE_TEXT.search(text):
            result[key]=text
        elif text:
            result[key]='已脱敏'
    return result


def event_time(value):
    if type(value) not in (int,float,str):return None
    if isinstance(value,str) and not re.fullmatch(r'\d{10,13}(?:\.\d{1,6})?',value):return None
    stamp=float(value)
    if 10**12<=stamp<10**13:return stamp/1000
    if 10**9<=stamp<10**10:return stamp
    return None


def decode(body, sent):
    batch=isinstance(body,dict) and 'evts' in body
    rows=body.get('evts') if batch else body if isinstance(body,list) else body.get('events',[body]) if isinstance(body,dict) else []
    result=[]
    if not isinstance(rows,list):return result
    for index,row in enumerate(rows[:256]):
        if not isinstance(row,dict):continue
        kind=row.get('type' if batch else 'event')
        if not isinstance(kind,str) or not _EVENT.fullmatch(kind):continue
        attrs={**redacted_fields(row),**redacted_fields(row.get('extraData')),**redacted_fields(row.get('extAttr'))}
        contents=row.get('conts',[]) if batch else [row.get('properties',row)]
        fields=[]
        if isinstance(contents,list):
            fields=[{**attrs,**redacted_fields(item.get('extraData')),**redacted_fields(item)}
                    for item in contents[:64] if isinstance(item,dict)]
        name=row.get('eventName','')
        result.append({'index':index,'event':kind,'name':name if isinstance(name,str) and _TEXT.fullmatch(name) and not _PRIVATE_TEXT.search(name) else '',
                       'time':event_time(row.get('time')),'sent':sent,'fields':attrs,'contents':fields or ([attrs] if attrs else [])})
    return result


def matching_request(target,url,method):
    if method!='POST' or (url.scheme,url.netloc,url.path)!=(target.scheme,target.netloc,target.path):return False
    expected=parse_qs(target.query,keep_blank_values=True)
    expected.pop('type',None)
    actual=parse_qs(url.query,keep_blank_values=True)
    return all(actual.get(key)==values for key,values in expected.items())


def request_records(body,sent,url,raw):
    group=parse_qs(url.query).get('type',[''])[0]
    rid=hashlib.sha256(('%0.6f'%sent+'\n'+url.geturl()+'\n'+raw).encode()).hexdigest()[:16]
    return [dict(row,reqId=rid,method='POST',endpoint=url._replace(query='',fragment='').geturl(),
                 requestType=group if _EVENT.fullmatch(group) else '') for row in decode(body,sent)]


def receipt(status,reply):
    code=reply.get('code') if isinstance(reply,dict) and type(reply.get('code')) is int else None
    msg=reply.get('msg') if isinstance(reply,dict) else None
    ack='回执未识别'
    if status is not None and (status>=400 or code is not None):
        ack='已接收' if 200<=status<300 and code==0 else '上报失败'
    return dict(httpStatus=status,businessCode=code,responseMessage=msg if msg in ('success','ok','OK','成功','操作成功') else '',ack=ack)


def request(flow):
    target=urlsplit(os.environ['TRACKING_EVENT_URL'])
    url=urlsplit(flow.request.pretty_url)
    if url.netloc==target.netloc and url.path==target.path:mark('matched_path')
    if not matching_request(target,url,flow.request.method):return
    mark('matched_query')
    try:
        raw=flow.request.get_text(strict=True)
        records=request_records(json.loads(raw),flow.request.timestamp_start,url,raw)
    except (ValueError,UnicodeError):return
    flow.metadata['tracking_rows']=records


def save(flow,details):
    rows=flow.metadata.pop('tracking_rows',[])
    if rows:
        with open(os.environ['TRACKING_ROWS'],'a',encoding='utf8') as f:
            for row in rows:
                print(json.dumps(dict(row,**details)),file=f)


def response(flow):
    if not flow.metadata.get('tracking_rows'):return
    try:reply=json.loads(flow.response.get_text(strict=True))
    except (ValueError,UnicodeError):reply=None
    save(flow,receipt(flow.response.status_code,reply))


def error(flow):
    save(flow,dict(receipt(None,None),ack='未取得响应'))
