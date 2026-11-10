"""Versioned standard cases. Excel carries editable data only; JSON owns the revision."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from zipfile import ZipFile


FIELDS = ('id', 'version', 'source', 'title', 'risk', 'precondition', 'steps', 'expected', 'cleanup', 'layer', 'status')
TITLES = {'id':'用例 ID', 'expected_version':'预期版本', 'source':'需求来源', 'title':'用例标题',
          'risk':'风险', 'precondition':'前置条件', 'expected':'预期结果', 'cleanup':'清理',
          'layer':'自动化层', 'status':'状态'}
HEADERS = tuple(TITLES)
STEP_HEADERS = ('用例 ID', '步骤序号', '步骤')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
MAX_CASES = 5000
MAX_TEXT = 30000
MAX_STEPS = 100
MAX_ROWS = 50000


def _text(value, name, allow_empty=False):
    if not isinstance(value, str) or len(value)>MAX_TEXT or (not allow_empty and not value.strip()):
        raise ValueError('字段为空、类型错误或过长：'+name)
    # Reject controls unsupported in OOXML so that JSON and Excel accept the same data.
    if any(ord(c)<32 and c not in '\n\t\r' or 0xD800<=ord(c)<=0xDFFF or ord(c) in (0xFFFE,0xFFFF) for c in value):
        raise ValueError('包含不可交换字符：'+name)
    if len(value.encode('utf-16-le'))//2 > 32767:
        raise ValueError('字段超出 Excel 单元格容量：'+name)


def _check_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):
        raise ValueError('无效用例 ID')


def validate(document):
    if not isinstance(document,dict) or set(document)!={'schema_version','cases'} or type(document['schema_version']) is not int or document['schema_version']!=1:
        raise ValueError('不支持的用例文件版本')
    cases=document['cases']
    if not isinstance(cases,list) or len(cases)>MAX_CASES:
        raise ValueError('用例数量或类型无效')
    seen=set()
    steps_total=0
    for case in cases:
        if not isinstance(case,dict) or set(case)!=set(FIELDS):
            raise ValueError('用例字段不完整或含未知字段')
        cid=case['id']
        _check_id(cid)
        if cid in seen:
            raise ValueError('重复用例 ID：'+cid)
        seen.add(cid)
        if type(case['version']) is not int or not 1<=case['version']<=2147483647:
            raise ValueError('无效用例版本：'+cid)
        for field in ('source','title','risk','expected','precondition','cleanup'):
            _text(case[field],field,field in ('precondition','cleanup'))
        if case['status'] not in ('draft','pending_review') or case['layer'] not in ('api','ui','manual'):
            raise ValueError('自动化层或审核状态无效：'+cid)
        steps=case['steps']
        if not isinstance(steps,list) or not 1<=len(steps)<=MAX_STEPS:
            raise ValueError('用例步骤无效：'+cid)
        for step in steps:
            _text(step,'步骤')
        steps_total+=len(steps)
    if steps_total>MAX_ROWS:
        raise ValueError('步骤总数超过限制')
    return document


def _sheet(workbook, name, headers, widths):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    sheet=workbook.create_sheet(name)
    sheet.append(headers)
    for cell,width in zip(sheet[1],widths):
        cell.font=Font(name='Calibri',bold=True,color='FFFFFF')
        cell.fill=PatternFill('solid',fgColor='165C52')
        cell.alignment=Alignment(vertical='center')
        sheet.column_dimensions[get_column_letter(cell.column)].width=width
    sheet.row_dimensions[1].height=24
    sheet.freeze_panes='C2' if name=='标准用例' else 'B2'
    return sheet


def _append(sheet,values):
    from openpyxl.cell import Cell
    from openpyxl.styles import Alignment
    cells=[]
    for value in values:
        cell=Cell(sheet,value=value)
        if isinstance(value,str):
            # Literal text must survive export even when a title starts with '='.
            cell.data_type='s'
        cell.alignment=Alignment(vertical='top',wrap_text=True)
        cells.append(cell)
    sheet.append(cells)


def write_xlsx(document,target):
    from openpyxl import Workbook
    validate(document)
    wb=Workbook()
    wb.remove(wb.active)
    cases=_sheet(wb,'标准用例',tuple(TITLES.values()),(38,12,48,42,26,46,62,32,14,20))
    steps=_sheet(wb,'操作步骤',STEP_HEADERS,(38,12,95))
    for c in document['cases']:
        _append(cases,[c['version' if k=='expected_version' else k] for k in HEADERS])
        for n,step in enumerate(c['steps'],1):
            _append(steps,(c['id'],n,step))
    for sheet in (cases,steps):
        sheet.auto_filter.ref=sheet.dimensions
    wb.save(target)
    wb.close()


def _rows(sheet,header,limit):
    # Ignore workbook dimension metadata; bounded real iteration guards forged dimensions.
    sheet.reset_dimensions()
    rows=sheet.iter_rows()
    header_seen=False
    for index,row in enumerate(rows):
        if index>limit:
            raise ValueError('表格行数超过限制')
        if len(row)>len(header) or any(c.data_type in ('f','e') for c in row):
            raise ValueError('工作表存在多余列、公式或错误单元格')
        values=[c.value for c in row]+[None]*(len(header)-len(row))
        if index==0:
            header_seen=True
            if tuple(values)!=header:
                raise ValueError('工作表表头或版本不符')
        elif any(v is not None for v in values):
            yield values
    if not header_seen:
        raise ValueError('缺少表头')


def _size_check(stream):
    from zipfile import BadZipFile
    try:
        with ZipFile(stream) as archive:
            parts=archive.infolist()
            if len(parts)>120 or sum(p.file_size for p in parts)>20*1024*1024 or any(p.flag_bits&1 for p in parts):
                raise ValueError('Excel 超出导入限制')
    except BadZipFile as exc:
        raise ValueError('Excel 不是有效的工作簿') from exc
    if hasattr(stream,'seek'):
        stream.seek(0)


def _read_xlsx(stream):
    from openpyxl import load_workbook
    _size_check(stream)
    wb=load_workbook(stream,read_only=True,data_only=False,keep_links=False)
    try:
        if wb.sheetnames!=['标准用例','操作步骤']:
            raise ValueError('工作表结构不正确')
        by_id={}
        for values in _rows(wb['标准用例'],tuple(TITLES.values()),MAX_CASES):
            c=dict(zip(HEADERS,values))
            cid=c['id']
            _check_id(cid)
            if cid in by_id:
                raise ValueError('重复用例 ID：'+cid)
            for key in ('precondition','cleanup'):
                if c[key] is None:
                    c[key]=''
            c['steps']=[]
            by_id[cid]=c
        by_step={}
        for cid,n,step in _rows(wb['操作步骤'],STEP_HEADERS,MAX_ROWS):
            _check_id(cid)
            if cid not in by_id or type(n) is not int or not 1<=n<=MAX_STEPS or (cid,n) in by_step:
                raise ValueError('操作步骤 ID 或序号无效')
            _text(step,'步骤')
            by_step[cid,n]=step
        for (cid,n),step in sorted(by_step.items()):
            steps=by_id[cid]['steps']
            if n!=len(steps)+1:
                raise ValueError('操作步骤序号不连续：'+cid)
            steps.append(step)
        rows=list(by_id.values())
        for row in rows:
            _validate_row(row)
        return rows
    finally:
        wb.close()


def read_xlsx(stream):
    from openpyxl.utils.exceptions import InvalidFileException
    from xml.etree.ElementTree import ParseError
    from zipfile import BadZipFile

    try:
        return _read_xlsx(stream)
    except (BadZipFile, InvalidFileException, KeyError, TypeError, IndexError, OverflowError,
            OSError, ParseError, SyntaxError, EOFError) as exc:
        raise ValueError('Excel 工作簿损坏或结构无效') from exc


def _validate_row(row):
    if not isinstance(row,dict) or 'expected_version' not in row or 'version' in row:
        raise ValueError('缺少预期版本')
    revision=row['expected_version']
    if type(revision) is not int or not 0<=revision<=2147483647:
        raise ValueError('预期版本无效')
    record={k:v for k,v in row.items() if k!='expected_version'}
    record['version']=max(1,revision)
    validate({'schema_version':1,'cases':[record]})
    return record


def merge_rows(current,rows):
    validate(current)
    by_id={c['id']:c for c in current['cases']}
    seen=set()
    for row in rows:
        record=_validate_row(row)
        cid=record['id']
        if cid in seen:
            raise ValueError('重复用例 ID：'+cid)
        seen.add(cid)
        prior=by_id.get(cid)
        old=prior['version'] if prior else 0
        if old!=row['expected_version']:
            raise ValueError('用例版本冲突：'+cid)
        if prior!=record:
            record['version']=old+1
            by_id[cid]=record
    return validate({'schema_version':1,'cases':list(by_id.values())})


def _save(path,write):
    path=Path(path)
    tmp=NamedTemporaryFile(mode='wb',dir=path.parent,prefix='.standard-cases-',delete=False)
    try:
        with tmp:
            write(tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name,path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _unique_object(pairs):
    result={}
    for k,v in pairs:
        if k in result:
            raise ValueError('重复 JSON 字段：'+k)
        result[k]=v
    return result


def parse_json(raw):
    if isinstance(raw,bytes):
        raw=raw.decode('utf-8')
    if not isinstance(raw,str):
        raise ValueError('用例数据需要 UTF-8 JSON')
    try:
        return validate(json.loads(raw,object_pairs_hook=_unique_object))
    except RecursionError as exc:
        raise ValueError('JSON 嵌套超过限制') from exc


def load_json(path):
    return parse_json(Path(path).read_bytes())


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['export','import'])
    parser.add_argument('json',type=Path,help='标准 JSON 用例库')
    parser.add_argument('xlsx',type=Path,help='Excel 交换文件')
    args=parser.parse_args(argv)
    if args.json.resolve()==args.xlsx.resolve():
        parser.error('文件路径必须不同')
    try:
        if args.action=='export':
            data=load_json(args.json)
            _save(args.xlsx,lambda out:write_xlsx(data,out))
        else:
            rows=read_xlsx(args.xlsx)
            # The lock belongs to the stable filename; atomic replacement changes the JSON inode.
            path=args.json
            with path.with_suffix(path.suffix+'.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                current=load_json(path)
                result=merge_rows(current,rows)
                _save(path,lambda out:out.write((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8')))
    except (ValueError, OSError) as exc:
        parser.exit(2,str(exc)+'\n')


if __name__=='__main__':
    main()
