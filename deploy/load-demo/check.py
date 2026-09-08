#!/usr/bin/env python3
"""Local low-impact checks only. Does not run stress, CPU, memory or failures."""
import json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
for path,key in [('/health',None),('/demo/ok','api'),('/metrics','metrics')]:
    headers={}
    if key:headers['Authorization']='Bearer '+(root/'secrets'/(key+'_token')).read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:18080'+path,headers=headers)
    with urllib.request.urlopen(req,timeout=5) as response:
        data=response.read().decode()
        print(path,response.status)
        if path=='/metrics':print(data)
print('仅证明本机服务与自身cgroup指标可读，不等于平台/专用节点可达。')
