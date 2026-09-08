"""Isolated, bounded demonstration target. Never proxy requests or call AI."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CGROUP = Path('/sys/fs/cgroup')
LABELS = '{id="/load-demo",name="load-demo",container="load-demo"}'


def metrics(root=CGROUP):
    """Actual namespaced cgroup v2 counters; missing files fail, never fake zero."""
    cpu = dict(line.split() for line in (root/'cpu.stat').read_text().splitlines())
    mem = dict(line.split() for line in (root/'memory.stat').read_text().splitlines())
    current = int((root/'memory.current').read_text())
    inactive = int(mem['inactive_file'])
    quota, period = (root/'cpu.max').read_text().split()
    limit = (root/'memory.max').read_text().strip()
    values = [('container_cpu_usage_seconds_total', int(cpu['usage_usec']) / 1000000),
              ('container_memory_working_set_bytes', max(0, current-inactive)),
              ('demo_cgroup_memory_current_bytes', current)]
    if quota != 'max':
        values += [('container_spec_cpu_quota', int(quota)), ('container_spec_cpu_period', int(period))]
    if limit != 'max': values += [('container_spec_memory_limit_bytes', int(limit))]
    return ''.join(f'{name}{LABELS} {value}\n' for name,value in values)


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.work = threading.BoundedSemaphore(4)
        self.memory = None
        self.memory_until = 0
        self.pause_until = 0
        self.fault = (None, 0)
        self.closed = threading.Event()
        self.cleaner = threading.Thread(target=self.clean, daemon=True)
        self.cleaner.start()

    def clean(self):
        while not self.closed.wait(.5):
            with self.lock:
                if time.monotonic() >= self.memory_until: self.memory = None

    def hold_memory(self):
        with self.lock:
            # One allocation per container, not per request. Fixed TTL is NOT
            # extended by repeated calls, so sustained load cannot pin it forever.
            if self.memory is None:
                self.memory = bytearray(64 * 1024 * 1024)
                self.memory_until = time.monotonic() + 30
            return len(self.memory)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, tokens, root=CGROUP):
        if any(not isinstance(t,str) or len(t)<32 for t in tokens.values()) or len(set(tokens.values())) != 3:
            raise ValueError('需要三个不同的至少32字符令牌')
        self.tokens, self.root = tokens,root
        self.state = State()
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(address,Handler)

    def process_request(self, request, address):
        request.settimeout(3)
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try: super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()

    def server_close(self):
        self.state.closed.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'LoadDemo/1.0'
    def log_message(self,*args): pass  # Never log credentials/URLs.

    def reply(self,status,payload,content_type='application/json; charset=utf-8'):
        body = payload.encode() if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close')
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        self.close_connection = True

    def authorized(self,kind):
        expected = 'Bearer '+self.server.tokens[kind]
        if not hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):
            self.reply(401,{'code':401,'message':'未授权'})
            return False
        return True

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/health':return self.reply(200,{'ok':True,'service':'独立压测演示服务','not_business':True})
        if path=='/metrics':
            if not self.authorized('metrics'):return
            if time.monotonic()<self.server.state.pause_until:return self.reply(503,{'code':503,'message':'演示采样暂缺，最长120秒后恢复'})
            try: data=metrics(self.server.root)
            except (OSError,ValueError,KeyError):return self.reply(503,{'code':503,'message':'自身cgroup v2指标不可用；不生成模拟资源数据'})
            return self.reply(200,data,'text/plain; version=0.0.4; charset=utf-8')
        if not self.authorized('api'):return
        if urlsplit(self.path).query:return self.reply(400,{'code':400,'message':'演示接口不接受动态负载参数'})
        allowed={'/demo/ok','/demo/slow','/demo/http-error','/demo/business-error','/demo/cpu','/demo/memory','/demo/switchable'}
        if path not in allowed:return self.reply(404,{'code':404})
        if not self.server.state.work.acquire(blocking=False):return self.reply(429,{'code':429,'message':'演示服务并发保护，最多4个请求工作槽'})
        try:
            if path=='/demo/slow':time.sleep(.6)
            if path=='/demo/cpu':
                until=time.monotonic()+.15
                value=b'demo'
                while time.monotonic()<until:value=hashlib.sha256(value).digest()
            allocated=self.server.state.hold_memory() if path=='/demo/memory' else None
            fault, expires = self.server.state.fault
            active = fault if path == '/demo/switchable' and time.monotonic() < expires else None
            status=500 if path=='/demo/http-error' or active=='http-error' else 200
            code=1001 if path=='/demo/business-error' or active=='business-error' else 500 if status==500 else 0
            self.reply(status,{'code':code,'message':'受控演示，非真实业务','data':{'path':path,'allocated_bytes':allocated}})
        finally:self.server.state.work.release()

    def do_POST(self):
        if not self.authorized('admin'):return
        path=urlsplit(self.path).path
        if path=='/admin/pause-metrics':self.server.state.pause_until=time.monotonic()+120
        elif path in ('/admin/http-error','/admin/business-error'):
            self.server.state.fault=(path.rsplit('/',1)[1],time.monotonic()+120)
        elif path=='/admin/reset':
            self.server.state.fault=(None,0)
            self.server.state.pause_until=0
            with self.server.state.lock:
                self.server.state.memory=None
                self.server.state.memory_until=0
        else:return self.reply(404,{'code':404})
        return self.reply(200,{'code':0,'message':'已设置；不影响FRP或其他服务'})


if __name__=='__main__':
    tokens={key:Path('/run/secrets/'+key+'_token').read_text().strip() for key in ('api','metrics','admin')}
    # cgroup namespace must refer to this container, not the host root.
    if Path('/proc/self/cgroup').read_text().strip()!='0::/':raise SystemExit('需要私有cgroup v2命名空间')
    metrics()
    server=Server(('0.0.0.0',8080),tokens)
    print('Load demo started on 8080; cgroup v2 only; bounded worker/memory; no AI calls',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
