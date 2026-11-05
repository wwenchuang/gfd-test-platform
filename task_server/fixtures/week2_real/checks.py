"""Independent, local processing checks based on a captured feedback response."""
import copy
import json


def extract(item):
    if not isinstance(item, dict): raise ValueError('not_object')
    for key in ('sn', 'name', 'children', 'order_related'):
        if key not in item: raise ValueError('missing_field')
    if not isinstance(item['sn'], str) or not item['sn'].strip(): raise ValueError('invalid_sn')
    if not isinstance(item['name'], str) or not item['name'].strip(): raise ValueError('invalid_name')
    if not isinstance(item['children'], list): raise ValueError('invalid_children')
    if type(item['order_related']) is not bool: raise ValueError('invalid_order_related')
    return {'sn': item['sn'], 'name': item['name'], 'children_count': len(item['children']),
            'order_related': item['order_related']}


def run_checks(evidence):
    checks = []
    def check(name, condition):
        checks.append({'id': name, 'status': 'passed' if condition else 'failed'})
    body = evidence['body']
    check('http_status', evidence['http_status'] == 200)
    check('code_integer', type(body.get('code')) is int)
    check('code_zero_observed', body.get('code') == 0 and type(body.get('code')) is int)
    rows = body.get('data')
    check('data_list', isinstance(rows, list))
    if not isinstance(rows, list) or not rows:
        for name in ('item_object', 'required_fields', 'sn_string', 'name_string', 'children_list',
                     'order_related_boolean', 'extract_count', 'extract_preserves_input',
                     'invalid_sn_rejected', 'invalid_name_rejected', 'invalid_children_rejected',
                     'invalid_order_related_rejected', 'missing_field_rejected'):
            checks.append({'id': name, 'status': 'blocked'})
        return checks
    check('item_object', all(isinstance(item, dict) for item in rows))
    check('required_fields', all(isinstance(item, dict) and all(key in item for key in ('sn','name','children','order_related')) for item in rows))
    check('sn_string', all(isinstance(item, dict) and isinstance(item.get('sn'), str) and bool(item['sn'].strip()) for item in rows))
    check('name_string', all(isinstance(item, dict) and isinstance(item.get('name'), str) and bool(item['name'].strip()) for item in rows))
    check('children_list', all(isinstance(item, dict) and isinstance(item.get('children'), list) for item in rows))
    check('order_related_boolean', all(isinstance(item, dict) and type(item.get('order_related')) is bool for item in rows))
    before = copy.deepcopy(rows)
    try: converted = [extract(item) for item in rows]
    except ValueError: converted = []
    check('extract_count', len(converted) == len(rows))
    check('extract_preserves_input', before == rows)
    if not converted:
        for name in ('invalid_sn_rejected', 'invalid_name_rejected', 'invalid_children_rejected', 'invalid_order_related_rejected', 'missing_field_rejected'):
            checks.append({'id': name, 'status': 'blocked'})
        return checks
    for name, key, invalid in [('invalid_sn_rejected','sn',None), ('invalid_name_rejected','name',''),
                                ('invalid_children_rejected','children',None), ('invalid_order_related_rejected','order_related','false'),
                                ('missing_field_rejected','sn',None)]:
        modified = copy.deepcopy(rows[0])
        if name == 'missing_field_rejected': del modified[key]
        else: modified[key] = invalid
        try: extract(modified)
        except ValueError: rejected = True
        else: rejected = False
        check(name, rejected)
    return checks
