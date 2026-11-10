"""Map a frozen AI draft to an editable, explicitly unreviewed set of manual cases."""
import io
import json
import re

from task_server.services import requirement_design, standard_cases

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def document(source, run):
    result = run['result']
    point_refs = {point['id']: point['refs'] for point in result['points']}
    cases = []
    for case in result['cases']:
        references = list(dict.fromkeys(
            ref for point in case['point_ids'] for ref in point_refs[point]
        ))
        cases.append({
            'id': run['id'] + ':' + case['id'], 'version': 1,
            'source': f"{source['title']} | {source['version']} | {source['id']} | {', '.join(references)}",
            'title': case['title'], 'risk': '待评估', 'precondition': case['precondition'],
            'steps': case['steps'], 'expected': case['expected'], 'cleanup': '待确认',
            'layer': 'manual', 'status': 'pending_review',
        })
    if not cases:
        raise ValueError('该草稿没有可导出的用例')
    data = {'schema_version': 1, 'cases': cases}
    standard_cases.validate(data)
    return data


def export(owner, source_id, run_id, fmt):
    if not re.fullmatch(r'[0-9a-f]{64}', source_id) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('材料或生成编号无效')
    if fmt not in ('json', 'xlsx'):
        raise ValueError('可选格式为 JSON 或 Excel')
    data = document(*requirement_design.completed_design(owner, source_id, run_id))
    name = 'test-cases-' + run_id + '.' + fmt
    if fmt == 'json':
        return 'application/json; charset=utf-8', name, json.dumps(data, ensure_ascii=False, indent=2).encode()
    stream = io.BytesIO()
    standard_cases.write_xlsx(data, stream)
    return XLSX, name, stream.getvalue()


def acceptance():
    """Expose only verified counts, without project records or replay payloads."""
    from task_server.services import requirement_evaluation_view as evaluation
    try:
        stored = evaluation._published_file('standard-case-acceptance.json')
    except (evaluation.Unavailable, requirement_design.Missing):
        return None
    fields = ('real_case_count', 'stress_row_count', 'offline_controls', 'eligible_count', 'generated')
    if not isinstance(stored, dict) or any(type(stored.get(key)) is not int or stored[key] < 0 for key in fields):
        return None
    return {key:stored[key] for key in fields}
