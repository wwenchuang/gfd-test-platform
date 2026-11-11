"""Optional host context for a local macOS load generator; does not send requests."""
import argparse
import csv
import ctypes as C
import json
import re
import subprocess
import time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
mach=C.CDLL('/usr/lib/libSystem.B.dylib');host=mach.mach_host_self()
def cpu():
    ticks=(C.c_uint*4)();count=C.c_uint(4)
    if mach.host_statistics(host,3,C.byref(ticks),C.byref(count))!=0:raise RuntimeError('could not read HOST_CPU_LOAD_INFO')
    return list(ticks)
prev=cpu();first=time.monotonic()
with args.output.open('x',newline='') as stream:
    out=csv.writer(stream);out.writerow(['timeStamp','cpu_busy_pct','free_pages_bytes','speculative_pages_bytes','active_pages_bytes','wired_pages_bytes'])
    while time.monotonic()-first<22*60:
        time.sleep(5);now=cpu();d=[(n-o)%(2**32) for n,o in zip(now,prev)];prev=now
        if not sum(d):continue
        vm=subprocess.check_output(['vm_stat'],text=True)
        def pages(label):
            match=re.search('^'+re.escape(label)+r':\s*([0-9]+)\.',vm,re.M);return int(match[1])
        size=int(re.search(r'page size of (\d+) bytes',vm)[1])
        out.writerow([time.time_ns()//1_000_000,round(100*(1-d[2]/sum(d)),3),*[pages(n)*size for n in ('Pages free','Pages speculative','Pages active','Pages wired down')]])
        stream.flush()
        end=args.run/'retest/metadata.json'
        if end.is_file() and json.loads(end.read_bytes()).get('recovery_end_ms'):break
