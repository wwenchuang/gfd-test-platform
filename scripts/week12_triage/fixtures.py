"""Observe four faults using disposable loopback endpoints, without a business system or paid model call."""
import http.client
import json
import threading
import socket
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        routes = {'/server-error':(500,b'{}'), '/expired':(401,b'{"code":4009}'),
                  '/page-before':(200,'<button>查询</button>'.encode()),
                  '/page-after':(200,'<button>搜索</button>'.encode())}
        status, body = routes.get(self.path,(404,b''))
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == '/model':
            # Accept the model request, then hold its response beyond the caller's deadline.
            self.server.entered.set()
            self.server.release.wait(timeout=3)


class Buttons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels = []
        self.active = False

    def handle_starttag(self, tag, _attrs):
        self.active = tag == 'button'

    def handle_endtag(self, _tag):
        self.active = False

    def handle_data(self, data):
        if self.active:
            self.labels.append(data)


def evaluate():
    rows = []
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        server.entered = threading.Event()
        server.release = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def get(path):
            conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            try:
                conn.request('GET',path)
                response = conn.getresponse()
                return response.status,response.read(512)
            finally:
                conn.close()
        def record(id_, expected, observed):
            rows.append({'id':id_,'expected':expected,'observed':observed,'verified':expected == observed,'source':'isolated','evaluator':'fixture_rule'})
        try:
            record('http_500','HTTP 500','HTTP '+str(get('/server-error')[0]))
            status, body = get('/expired')
            record('auth_expired','HTTP 401 / code 4009','HTTP %s / code %s' % (status,json.loads(body)['code']))
            before, after = Buttons(), Buttons()
            before.feed(get('/page-before')[1].decode())
            after.feed(get('/page-after')[1].decode())
            record('page_renamed','查询 → 搜索', '、'.join(before.labels)+' → '+'、'.join(after.labels))
            conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=.2)
            try:
                conn.request('POST','/model',body=b'{}',headers={'Content-Type':'application/json'})
                conn.getresponse()
                outcome = '模型夹具返回了响应'
            except (TimeoutError, socket.timeout):
                outcome = '模型夹具已收到请求，读取超时' if server.entered.is_set() else '请求未到达夹具'
            finally:
                conn.close()
            record('model_timeout','模型夹具已收到请求，读取超时',outcome)
        finally:
            server.release.set()
            server.shutdown()
            thread.join(2)
    return rows
