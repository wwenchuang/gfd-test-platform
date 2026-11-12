"""mitmdump addon. Retain only telemetry fields from the configured reporting URL."""
import json
import os
from urllib.parse import urlsplit

def request(flow):
    target=urlsplit(os.environ['TRACKING_EVENT_URL'])
    url=urlsplit(flow.request.pretty_url)
    if (url.scheme,url.netloc,url.path)!=(target.scheme,target.netloc,target.path):return
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
