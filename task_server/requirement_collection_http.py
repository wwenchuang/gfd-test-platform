"""Personal standard-case drafts imported from or exported to the workbench."""
import base64
import io
import json
from xml.etree.ElementTree import ParseError
from xml.parsers import expat
from zlib import error as ZlibError
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from task_server.services import requirement_design as design, standard_cases
from task_server.services.requirement_standard_export import XLSX


PATH = '/api/platform/requirement-design/collection'
MAX_FILE = 2 * 1024 * 1024
MAX_UNPACKED = 20 * 1024 * 1024
MAX_COLLECTION = 1000


class Conflict(Exception):
    pass


def _read(db, owner):
    db.execute('CREATE TABLE IF NOT EXISTS standard_case_collections (owner TEXT PRIMARY KEY, document TEXT NOT NULL)')
    record = db.execute('SELECT document FROM standard_case_collections WHERE owner=?', (owner,)).fetchone()
    return json.loads(record[0]) if record else {'schema_version':1, 'cases':[]}


def read(owner):
    with design._db() as db:
        return _read(db, owner)


def _workbook(raw):
    def reject_dtd(*args):
        raise ValueError('Excel 文件含禁止的 XML 声明')
    with ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries)>120 or sum(part.file_size for part in entries)>MAX_UNPACKED or len({part.filename for part in entries})!=len(entries):
            raise ValueError('Excel 文件超出解压限制或含重名文件')
        for part in entries:
            if part.flag_bits & 1 or part.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                raise ValueError('不支持该 Excel 文件的压缩方式')
            if not part.filename.lower().endswith(('.xml', '.rels')):
                continue
            parser=expat.ParserCreate()
            parser.StartDoctypeDeclHandler=reject_dtd
            with archive.open(part) as stream:
                parser.ParseFile(stream)
    return standard_cases.read_xlsx(io.BytesIO(raw))


def _rows(body):
    if not isinstance(body,dict) or set(body)!={'format','file_base64'} or body['format'] not in ('json','xlsx'):
        raise ValueError('请选择标准用例 JSON 或 Excel 文件')
    encoded=body['file_base64']
    if not isinstance(encoded,str) or not encoded or len(encoded)>4*((MAX_FILE+2)//3):
        raise ValueError('文件须在 2 MiB 以内')
    try:
        raw=base64.b64decode(encoded,validate=True)
    except ValueError:
        raise ValueError('文件编码错误') from None
    if len(raw)>MAX_FILE:
        raise ValueError('文件须在 2 MiB 以内')
    try:
        if body['format']=='xlsx':
            rows=_workbook(raw)
        else:
            document=standard_cases.parse_json(raw)
            rows=[]
            for case in document['cases']:
                row=case.copy()
                row['expected_version']=row.pop('version')
                rows.append(row)
    except (BadZipFile, expat.ExpatError, ParseError, ZlibError, UnicodeError, LookupError, TypeError, OverflowError, RecursionError, OSError):
        raise ValueError('无法读取标准用例文件') from None
    if not rows or len(rows)>MAX_COLLECTION:
        raise ValueError('一次可导入 1 至 1000 条用例')
    return rows


def import_file(owner,username,body):
    rows=_rows(body)
    with design._db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not design.authorized(owner,username):
            raise PermissionError()
        current=_read(db,owner)
        existing={case['id']:case for case in current['cases']}
        added=updated=0
        for row in rows:
            previous=existing.get(row['id'])
            version=row['expected_version']
            if previous is None and version in (0,1):
                # Fresh generator exports start at 1. A later missing revision is a conflict.
                row['expected_version']=0
                added+=1
            elif previous is None or version!=previous['version']:
                raise Conflict(row['id'])
            elif any(value!=previous[key] for key,value in row.items() if key!='expected_version'):
                updated+=1
        merged=standard_cases.merge_rows(current,rows)
        text=json.dumps(merged,ensure_ascii=False,separators=(',',':'))
        if len(merged['cases'])>MAX_COLLECTION or len(text.encode())>MAX_FILE:
            raise ValueError('个人工作集最多 1000 条，标准 JSON 总大小最多 2 MiB')
        if added or updated:
            db.execute('INSERT INTO standard_case_collections VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET document=excluded.document',
                       (owner,text))
        return dict(total=len(merged['cases']),added=added,updated=updated,unchanged=len(rows)-added-updated)


def _download(owner,fmt):
    document=read(owner)
    if fmt=='json':
        return 'application/json; charset=utf-8','my-standard-cases.json',json.dumps(document,ensure_ascii=False,separators=(',',':')).encode()
    if fmt!='xlsx':
        raise ValueError('仅支持 JSON 和 Excel')
    stream=io.BytesIO()
    standard_cases.write_xlsx(document,stream)
    return XLSX,'my-standard-cases.xlsx',stream.getvalue()


def register_collection_routes(route_get,route_post,actor,serve):
    @route_get(PATH)
    def collection(handler,qs):
        def fetch(owner,username):
            if not qs:
                from task_server.services.requirement_standard_export import acceptance
                return {'total':len(read(owner)['cases']), 'acceptance':acceptance()}
            if set(qs)!={'format'}:
                raise ValueError('请求参数无效')
            return _download(owner,qs['format'])
        serve(handler,fetch,download=bool(qs))

    @route_post(PATH)
    def import_collection(handler,qs):
        identity=actor(handler)
        if not identity:
            return
        try:
            if qs:
                raise ValueError('请求参数无效')
            result=import_file(*identity,handler._body())
        except Conflict as exc:
            handler._json({'ok':False,'error':'用例版本冲突：'+str(exc),'id':str(exc)},409)
        except PermissionError:
            handler._json({'ok':False,'error':'账号权限已变化'},403)
        except ValueError as exc:
            handler._json({'ok':False,'error':str(exc)},400)
        else:
            handler._json(dict(ok=True,**result))
