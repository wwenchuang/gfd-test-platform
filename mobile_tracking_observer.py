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
    actual=parse_qs(url.query,keep_blank_values=True)
    if any(actual.get(key)!=values for key,values in expected.items()):return
    mark('matched_query')
    try:body=json.loads(flow.request.get_text(strict=True))
    except (ValueError,UnicodeError):return
    rows=body if isinstance(body,list) else body.get('events',[body]) if isinstance(body,dict) else []
    if not isinstance(rows,list):return
    with open(os.environ['TRACKING_ROWS'],'a',encoding='utf8') as f:
        for row in rows[:256]:
            if not isinstance(row,dict) or not isinstance(row.get('event'),str):continue
            props=row.get('properties',row)
            fields={k:str(props[k])[:160] for k in ('page','pageTitle','element','elementId') if isinstance(props,dict) and k in props}
            print(json.dumps({'event':row['event'][:100],'time':flow.request.timestamp_start,'fields':fields}),file=f)
