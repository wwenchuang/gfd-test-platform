"""Loopback ingress for verified change notifications behind the site's HTTPS proxy."""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_BODY = 128 * 1024
FEISHU_EVENTS = {'WorkitemCreateEvent', 'WorkitemUpdateEvent', 'WorkitemStatusEvent',
                 'WorkFlowNodeStatusEvent', 'WorkitemPersonAssignedEvent'}


def _verify(actual, key):
    expected = os.environ.get(key, '')
    if not expected or not isinstance(actual,str) or not hmac.compare_digest(actual.encode(),expected.encode()):
        raise PermissionError()


def receive(path, body, cfg, marker):
    if not isinstance(body,dict) or not isinstance(cfg,list) or len(cfg)!=2:
        raise ValueError()
    dev, ready = sorted(cfg,key=lambda c:c['mode'])
    if dev['mode']!='development_design' or ready['mode']!='qa_build_smoke' or dev['project_key']!=ready['project_key']:
        raise ValueError()
    if path=='/hooks/qa/figma':
        _verify(body.get('passcode'),'MIDSCENE_FIGMA_HOOK_PASSCODE')
        if body.get('event_type')=='PING':
            return
        if body.get('file_key') not in dev.get('figma_files',[]):
            raise PermissionError()
        if body.get('event_type') not in {'FILE_UPDATE','FILE_VERSION_UPDATE','DEV_MODE_STATUS_UPDATE'}:
            return
    elif path=='/hooks/qa/build-context':
        _verify(body.get('token'),'MIDSCENE_QA_PACKAGING_TOKEN')
        if ready.get('build_source')!='packaging_hook' or body.get('project_key')!=dev['project_key']:
            raise PermissionError()
        from .feishu_build_plan import build_context
        return build_context(dev,body.get('requirement_id'))
    elif path=='/hooks/qa/build':
        _verify(body.get('token'),'MIDSCENE_QA_PACKAGING_TOKEN')
        if ready.get('build_source')!='packaging_hook':
            raise PermissionError()
        from .feishu_build_plan import record_build
        record_build(ready,body.get('build'))
    elif path=='/hooks/qa/feishu-project':
        header, payload = body.get('header'),body.get('payload')
        if not isinstance(header,dict) or not isinstance(payload,dict):
            raise ValueError()
        _verify(header.get('token'),'MIDSCENE_FEISHU_HOOK_TOKEN')
        if payload.get('project_key')!=dev['project_key']:
            raise PermissionError()
        if header.get('event_type') not in FEISHU_EVENTS:
            return
    else:
        raise ValueError()
    # The callback contains no executable input. systemd coalesces pending wakes;
    # the worker re-reads the story, CI field, and exact Figma node using its own identity.
    marker.touch(mode=0o600)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ('/hooks/qa/figma','/hooks/qa/feishu-project','/hooks/qa/build','/hooks/qa/build-context'):
            return self.send_error(404)
        try:
            self.connection.settimeout(5)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=MAX_BODY:
                return self.send_error(413)
            body=json.loads(self.rfile.read(size))
            cfg=json.loads(Path(os.environ['MIDSCENE_FEISHU_REQUIREMENT_CONFIG']).read_text('utf-8'))
            result=receive(self.path,body,cfg,Path('/var/lib/midscene-requirement-events/pending'))
        except PermissionError:
            return self.send_error(403)
        except (ValueError,TypeError,KeyError,RecursionError):
            return self.send_error(400)
        except OSError:
            return self.send_error(503)
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"code":0, **({"data":result} if result else {})}).encode())


if __name__=='__main__':
    HTTPServer(('127.0.0.1',int(os.environ.get('MIDSCENE_REQUIREMENT_HOOK_PORT','18746'))),Handler).serve_forever()
