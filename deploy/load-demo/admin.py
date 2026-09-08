#!/usr/bin/env python3
"""Local, explicit, bounded demo-only administration; credentials never printed."""
import argparse,json,urllib.request
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['pause-metrics','http-error','business-error','reset'])
args=parser.parse_args()
token=(Path(__file__).resolve().parent/'secrets/admin_token').read_text().strip()
request=urllib.request.Request('http://127.0.0.1:18080/admin/'+args.action,method='POST',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(request,timeout=5) as response:print(response.status,json.load(response)['message'])
