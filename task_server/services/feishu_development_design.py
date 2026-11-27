"""Create a private reviewable design from a development-stage Feishu requirement."""
import base64
import hashlib
import re
import time
from urllib.parse import urlsplit

from . import knowledge_service, requirement_design, requirement_intake


def _prototype(cfg, link):
    if isinstance(link, dict):
        link = link.get('link') or link.get('url')
    if not isinstance(link, str) or not link.startswith(('https://www.figma.com/', 'https://figma.com/')):
        raise ValueError('figma_frame_link_required')
    file_key, node = knowledge_service.parse_figma_url(link)
    if file_key not in cfg['figma_files'] or not re.fullmatch(r'[0-9]+:[0-9]+', node):
        raise ValueError('figma_frame_out_of_scope')
    data = knowledge_service.figma_api_json('/files/' + file_key + '/nodes', {'ids':node})
    doc = ((data.get('nodes') or {}).get(node) or {}).get('document') or {}
    version = data.get('version')
    if doc.get('id') != node or not isinstance(version,str) or not version:
        raise ValueError('figma_frame_unavailable')
    exports = knowledge_service.figma_api_json('/images/' + file_key, {
        'ids':node, 'format':'png', 'version':version, 'scale':'1'})
    url = (exports.get('images') or {}).get(node)
    if not isinstance(url,str) or urlsplit(url).scheme != 'https':
        raise ValueError('figma_export_unavailable')
    blob = knowledge_service._urlopen_bytes(url, timeout=30, max_bytes=requirement_design.MAX_IMAGE, retries=1)
    # Use the same bounded PNG decode and metadata stripping as a workbench upload.
    png = requirement_design.clean_prototype_png(base64.b64encode(blob).decode('ascii'))
    ref = dict(file_key=file_key, node_id=node, version=version)
    return ref, png


def store_source(cfg, event, link):
    clauses = event['acceptance_criteria']
    meta = dict(source_type='excerpt', title=event['title'], app=event['app_id'], version=event['revision'],
                requirement_ref={key:event[key] for key in requirement_intake.IDS},
                prd_file_sha256=hashlib.sha256('\n'.join(clauses).encode()).hexdigest(),
                clauses=[dict(id='P'+str(i),text=text) for i,text in enumerate(clauses,1)], prototypes=[])
    images = {}
    if 'figma_field' in cfg:
        links = link if isinstance(link,list) else [link]
        if not 1<=len(links)<=8:
            raise ValueError('figma_frame_count_invalid')
        meta['figma_refs'] = []
        versions = {}
        seen = set()
        for i,url in enumerate(links,1):
            ref, png = _prototype(cfg,url)
            file_key = ref['file_key']
            if versions.setdefault(file_key,ref['version']) != ref['version']:
                raise ValueError('figma_changed_during_snapshot')
            node = (file_key,ref['node_id'])
            if node in seen:
                raise ValueError('duplicate_figma_frame')
            seen.add(node)
            vid='V'+str(i)
            meta['figma_refs'].append(ref)
            meta['prototypes'].append(dict(id=vid,file=f'prototype-{i}.png',sha256=hashlib.sha256(png).hexdigest(),
                                           caption=f"需求关联设计 {file_key} 节点 {ref['node_id']}"))
            images[vid] = png
    return requirement_design._store(cfg['user_id'],meta,images)


def design_request(sid,attempt=0):
    from .requirement_design_model import PROMPT_VERSION
    label=f'development-design/{sid}/{PROMPT_VERSION}'
    if attempt:
        label += '/retry-' + str(attempt)
    return hashlib.sha256(label.encode()).hexdigest()[:32]


def last_design(db,owner,sid):
    requests=[design_request(sid,i) for i in range(3)]
    return db.execute("""SELECT * FROM designs WHERE owner=? AND source=? AND request IN (?,?,?)
                      ORDER BY created DESC LIMIT 1""",(owner,sid,*requests)).fetchone()


def create_design(cfg, event, link):
    owner = cfg['user_id']
    sid = store_source(cfg,event,link)
    with requirement_design._db() as db:
        prior=last_design(db,owner,sid)
    request=prior['request'] if prior else design_request(sid)
    if prior and prior['status']=='failed':
        attempt=next(i for i in range(3) if design_request(sid,i)==request)
        if attempt<2 and time.time()-(prior['finished'] or prior['created']) >= (300,1800)[attempt]:
            request=design_request(sid,attempt+1)
    try:
        run = requirement_design.start(owner,cfg['username'],sid,request,wait=True)
    except requirement_design.Busy:
        return {'status':'design_busy'}
    return {'status':'design_' + run['status'], 'source_id':sid, 'design_id':run['id']}
