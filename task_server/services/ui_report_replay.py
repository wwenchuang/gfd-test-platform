"""Rebuild private replay from inert report data and a pinned trusted renderer."""
import base64
import gzip
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'"
# The renderer uses localStorage for theme preferences. Opaque sandbox origins
# intentionally have no browser storage; keep preferences only in this iframe.
STORAGE_SHIM = """<script>(()=>{for(const name of ['localStorage','sessionStorage']){const m=new Map();Object.defineProperty(window,name,{value:{getItem:k=>m.get(String(k))??null,setItem:(k,v)=>m.set(String(k),String(v)),removeItem:k=>m.delete(String(k)),clear:()=>m.clear(),key:i=>Array.from(m.keys())[i]??null,get length(){return m.size}}});}})();</script>"""
# Timeline selection updates URL hashes; srcdoc has no shareable URL. Keep state
# updates local and omit URL changes, which opaque sandbox origins reject.
HISTORY_SHIM = """<script>(()=>{for(const name of ['replaceState','pushState']){const original=history[name].bind(history);history[name]=(state,title)=>original(state,title);}})();</script>"""
DATA_TYPES = {'midscene_web_dump', 'midscene_test_run_dump', 'midscene-image'}


class ReportData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.current = None
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            attrs = dict(attrs)
            self.current = attrs if attrs.get('type') in DATA_TYPES else None
            self.parts = []

    def handle_data(self, data):
        if self.current is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.blocks.append((self.current, ''.join(self.parts)))
            self.current = None


@lru_cache(maxsize=1)
def _template():
    return gzip.decompress((Path(__file__).parent.parent/'assets/midscene-report-1.13.0.html.gz').read_bytes()).decode()


def build_replay_document(content):
    if len(content) > 80 * 1024 * 1024:
        raise ValueError('replay_too_large')
    parser = ReportData()
    parser.feed(content.decode('utf-8', errors='replace'))
    parser.close()
    blocks = []
    has_dump = False
    for attrs, value in parser.blocks:
        kind = attrs['type']
        if kind == 'midscene-image':
            value = value.strip()
            match = re.fullmatch(r'data:image/(?:png|jpeg|webp|gif);base64,([A-Za-z0-9+/=\r\n]+)', value)
            if not match:
                raise ValueError('replay_image_invalid')
            try:
                base64.b64decode(match.group(1), validate=False)
            except ValueError as error:
                raise ValueError('replay_image_invalid') from error
            identity = attrs.get('data-id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identity):
                raise ValueError('replay_image_invalid')
            extra = ' data-id="'+html.escape(identity, quote=True)+'"'
        else:
            try:
                data = json.loads(value)
            except (ValueError, RecursionError) as error:
                raise ValueError('replay_data_invalid') from error
            if not isinstance(data, dict):
                raise ValueError('replay_data_invalid')
            has_dump = True
            value = json.dumps(data, ensure_ascii=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
            extra = ''
            if kind == 'midscene_web_dump':
                extra = ' data-group-id="'+html.escape(attrs.get('data-group-id', ''), quote=True)+'" data-screenshot-mode="inline"'
        blocks.append('<script type="'+kind+'"'+extra+'>'+value+'</script>')
    if not has_dump:
        raise ValueError('replay_unavailable')
    template = _template().replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="'+CSP+'">'+STORAGE_SHIM+HISTORY_SHIM, 1)
    # SDK renderer reads inert JSON blocks anywhere in the document.
    return template.replace('</body>', ''.join(blocks)+'</body>', 1)
