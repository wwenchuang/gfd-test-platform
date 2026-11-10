"""Read the two published evaluation files. Raw calls and the blind key stay private."""
from contextlib import ExitStack
import json
import os
import stat

from task_server.services import requirement_design as design


class Unavailable(Exception):
    pass


def _published_file(name):
    directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    try:
        with ExitStack() as stack:
            directory = os.open(design._root(), directory_flags)
            stack.callback(os.close, directory)
            for part in ('requirement-design-materials', 'evaluation'):
                directory = os.open(part, directory_flags, dir_fd=directory)
                stack.callback(os.close, directory)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                limit = 4 * 1024 * 1024
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise Unavailable()
                data = stream.read(limit + 1)
                if len(data) > limit:
                    raise Unavailable()
                return json.loads(data)
    except FileNotFoundError:
        raise design.Missing() from None
    except (OSError, ValueError, RecursionError):
        raise Unavailable() from None


def _fields(row, keys):
    return {key: row[key] for key in keys.split() if key in row and
            (row[key] is None or type(row[key]) in (int, float, str, bool))}


def _strings(row, key):
    value = row.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Unavailable()
    return value


def _case(row, blind=False):
    result = _fields(row, ('label' if blind else 'id') + ' group title precondition expected')
    result.update(steps=_strings(row, 'steps'), refs=_strings(row, 'refs'))
    if blind:
        result['review'] = None
    return result


def _sources(group):
    return {'clauses': [_fields(v, 'id text') for v in group.get('clauses', [])],
            'prototypes': [_fields(v, 'id caption sha256') for v in group.get('prototypes', [])]}


def _group(group):
    result = _fields(group, 'id title sample_count prototype_count input_sha256 model_comparable')
    result.update(_sources(group))
    result['variants'] = []
    for variant in group['variants']:
        entry = _fields(variant, 'variant status model request_id response_id elapsed_ms point_count case_count question_count error started_at completed_at attempt_count revalidated')
        entry.update(usage=_fields(variant.get('usage') or {}, 'prompt_tokens completion_tokens total_tokens'),
                     referenced_source_ids=_strings(variant, 'referenced_source_ids'),
                     unreferenced_source_ids=_strings(variant, 'unreferenced_source_ids'))
        entry['rejected_attempts'] = [_fields(v, 'error elapsed_ms started_at request_id') for v in variant.get('rejected_attempts', [])]
        result['variants'].append(entry)
    result['observations'] = [dict(_fields(v, 'kind text'), refs=_strings(v, 'refs')) for v in group['observations']]
    result['questions'] = _strings(group, 'questions')
    return result


def published(*, blind=False):
    doc = _published_file('blind-review.json' if blind else 'report.json')
    try:
        if doc['version'] != 1:
            raise Unavailable()
        if blind:
            if doc['status'] != 'pending':
                raise Unavailable()
            return {'blind': {'version':1, 'status':'pending', 'items':[_case(v, blind=True) for v in doc['items']],
                              'sources':[dict(_fields(s, 'id title'), **_sources(s)) for s in doc.get('sources', [])]}}
        if doc['status'] not in ('complete', 'partial') or doc['human_review'] != 'pending':
            raise Unavailable()
        report = _fields(doc, 'version status candidate_count candidate_target target_met selected_variant sample_count prototype_count human_review prompt_version requested_model deduplication')
        report['prompt_sha256'] = _fields(doc.get('prompt_sha256', {}), 'zero few')
        parameters = doc.get('parameters', {})
        report['parameters'] = dict(_fields(parameters, 'max_tokens enable_thinking'),
                                    response_format=_fields(parameters.get('response_format', {}), 'type'))
        report['duplicate_ids'] = _strings(doc, 'duplicate_ids')
        report['groups'] = [_group(g) for g in doc['groups']]
        report['candidates'] = [_case(c) for c in doc['candidates']]
        report['blind_review'] = dict(_fields(doc['blind_review'], 'candidate_count'), status='pending', metrics=None)
        if report['candidate_count'] != len(report['candidates']):
            raise Unavailable()
        return {'report': report}
    except (KeyError, TypeError, AttributeError):
        raise Unavailable() from None
