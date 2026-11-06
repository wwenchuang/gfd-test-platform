"""Bounded comparison of saved response *definitions*, not runtime conformance.

This deliberately understands only a small subset of OpenAPI / JSON Schema.
Any unsupported construct marks the result incomplete; enum values and examples
are never returned. No remote references or production requests are made.
"""

import math
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping


_MAX_NODES = 1500
_MAX_DEPTH = 20
_MAX_ITEMS = 100
_MAX_FIELDS = 300
_MAX_ENUM = 100
_MAX_PATH = 240
_MAX_STATUS = 100
_ANNOTATIONS = frozenset({
    'title', 'description', 'example', 'examples', 'default', 'deprecated',
    'x-apifox-folder', 'x-apifox-orders',
})
_SUPPORTED = frozenset({'type', 'properties', 'required', 'items', 'enum', '$ref', 'additionalProperties'})


class _Comparison:
    def __init__(self, previous, candidate, budget):
        self.budget = budget
        self.previous_refs = previous.get('resolved_dependencies', {}) if isinstance(previous, Mapping) else {}
        self.candidate_refs = candidate.get('resolved_dependencies', {}) if isinstance(candidate, Mapping) else {}
        self.complete = True
        self.items = []
        self.nodes = 0

    def incomplete(self):
        self.complete = False

    def add(self, kind, status, path=''):
        if path is None or len(self.items) >= _MAX_ITEMS or self.budget['items'] >= 1000 or len(path) > _MAX_PATH:
            self.incomplete()
            return
        self.items.append({'kind': kind, 'status': status, 'path': path})
        self.budget['items'] += 1

    def ref(self, value, dependencies, seen):
        while isinstance(value, Mapping) and '$ref' in value:
            # JSON Schema siblings may constrain the resolved value. Do not
            # silently claim those constraints were compared.
            if set(value) - {'$ref'} - _ANNOTATIONS:
                self.incomplete()
                return None, seen
            self.nodes += 1
            self.budget['nodes'] += 1
            if self.nodes > _MAX_NODES or self.budget['nodes'] > 20000:
                self.incomplete()
                return None, seen
            key = value.get('$ref')
            if (not isinstance(key, str) or not key.startswith('#/') or key in seen
                    or len(seen) >= _MAX_DEPTH or not isinstance(dependencies, Mapping) or key not in dependencies):
                self.incomplete()
                return None, seen
            seen = seen | {key}
            value = dependencies[key]
        return value, seen

    def schema(self, previous, candidate, status, path='', left_seen=frozenset(), right_seen=frozenset(), depth=0):
        self.nodes += 1
        self.budget['nodes'] += 1
        if depth > _MAX_DEPTH or self.nodes > _MAX_NODES or self.budget['nodes'] > 20000:
            self.incomplete()
            return
        previous, left_seen = self.ref(previous, self.previous_refs, left_seen)
        candidate, right_seen = self.ref(candidate, self.candidate_refs, right_seen)
        if not isinstance(previous, Mapping) or not isinstance(candidate, Mapping):
            self.incomplete()
            return
        for schema in (previous, candidate):
            if set(schema) - _SUPPORTED - _ANNOTATIONS:
                self.incomplete()
            if schema.get('additionalProperties', True) is not True:
                self.incomplete()
        # Do not infer removed or added fields from incompatible types or
        # combinators. They can change the interpretation of all descendants.
        schema_type = previous.get('type')
        if (any('type' in value and value['type'] is None for value in (previous, candidate))
                or schema_type not in (None, 'object', 'array', 'string', 'number', 'integer', 'boolean')
                or previous.get('type') != candidate.get('type') or any(
                    key in schema for schema in (previous, candidate) for key in ('allOf', 'anyOf', 'oneOf', 'not')
                )):
            self.incomplete()
            return
        if schema_type != 'object' and any(
            key in value for value in (previous, candidate) for key in ('required', 'properties')
        ):
            self.incomplete()
            return
        if 'enum' in previous or 'enum' in candidate:
            left, right = previous.get('enum'), candidate.get('enum')
            try:
                def canonical(values):
                    if not isinstance(values, list) or not values or len(values) > _MAX_ENUM:
                        raise ValueError('invalid enum')
                    result = set()
                    for value in values:
                        if value is None:
                            result.add(('null', ''))
                        elif isinstance(value, bool):
                            result.add(('bool', str(value)))
                        elif isinstance(value, (int, float)):
                            if isinstance(value, float) and not math.isfinite(value):
                                raise ValueError('invalid number')
                            text = str(value)
                            if len(text) > 2048:
                                raise ValueError('number too large')
                            sign, digits, exponent = Decimal(text).as_tuple()
                            digits = list(digits)
                            if not any(digits):
                                sign, digits, exponent = 0, [0], 0
                            else:
                                while len(digits) > 1 and digits[-1] == 0:
                                    digits.pop()
                                    exponent += 1
                            result.add(('number', (sign, tuple(digits), exponent)))
                        elif isinstance(value, str) and len(value) <= 2048:
                            result.add(('string', value))
                        else:
                            # Composite enum values require full JSON Schema
                            # equality semantics, which this view does not implement.
                            raise ValueError('unsupported enum')
                    if len(result) != len(values):
                        raise ValueError('duplicate enum')
                    return result
                left_values = None if 'enum' not in previous else canonical(left)
                right_values = None if 'enum' not in candidate else canonical(right)
                if left_values != right_values:
                    self.add('enum_changed', status, path)
            except (TypeError, ValueError, InvalidOperation, RecursionError):
                self.incomplete()
        for value in (previous, candidate):
            required = value.get('required', [])
            properties = value.get('properties', {})
            if (not isinstance(required, list) or len(required) > _MAX_FIELDS
                    or any(not isinstance(name, str) or len(name) > _MAX_PATH for name in required)
                    or (all(isinstance(name, str) for name in required) and len(set(required)) != len(required))
                    or not isinstance(properties, Mapping) or len(properties) > _MAX_FIELDS
                    or any(not isinstance(name, str) or len(name) > _MAX_PATH for name in properties)):
                self.incomplete()
                return
        def child_path(name):
            if name == '*':
                self.incomplete()
                return None
            return path + '/' + name.replace('~', '~0').replace('/', '~1')
        prev_required, next_required = set(previous.get('required', [])), set(candidate.get('required', []))
        for name in sorted(prev_required - next_required):
            self.add('required_removed', status, child_path(name))
        for name in sorted(next_required - prev_required):
            self.add('required_added', status, child_path(name))
        left_props, right_props = previous.get('properties', {}), candidate.get('properties', {})
        if left_props.keys() != right_props.keys():
            # We report the declaration, but do not traverse a one-sided
            # schema or claim its constraints were fully compared.
            self.incomplete()
        for name in sorted(left_props.keys() - right_props.keys()):
            self.add('property_removed', status, child_path(name))
        for name in sorted(right_props.keys() - left_props.keys()):
            self.add('property_added', status, child_path(name))
        for name in sorted(left_props.keys() & right_props.keys()):
            next_path = child_path(name)
            if next_path is not None:
                self.schema(left_props[name], right_props[name], status, next_path, left_seen, right_seen, depth + 1)
        if 'items' in previous or 'items' in candidate:
            if schema_type != 'array' or 'items' not in previous or 'items' not in candidate:
                self.incomplete()
            else:
                self.schema(previous['items'], candidate['items'], status, path + '/*', left_seen, right_seen, depth + 1)

    def response(self, previous, candidate, status):
        previous, left_seen = self.ref(previous, self.previous_refs, frozenset())
        candidate, right_seen = self.ref(candidate, self.candidate_refs, frozenset())
        if not isinstance(previous, Mapping) or not isinstance(candidate, Mapping):
            self.incomplete()
            return
        for response in (previous, candidate):
            headers = response.get('headers', {})
            if not isinstance(headers, Mapping) or headers:
                self.incomplete()
            if set(response) - {'content', 'headers', 'description', 'x-apifox-name', 'x-apifox-ordering'}:
                self.incomplete()
        left, right = previous.get('content'), candidate.get('content')
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            self.incomplete()
            return
        if set(left) != {'application/json'} or set(right) != {'application/json'}:
            self.incomplete()
        lmedia, rmedia = left.get('application/json'), right.get('application/json')
        if not isinstance(lmedia, Mapping) or not isinstance(rmedia, Mapping) or 'schema' not in lmedia or 'schema' not in rmedia:
            self.incomplete()
            return
        if set(lmedia) - {'schema', 'example', 'examples'} or set(rmedia) - {'schema', 'example', 'examples'}:
            self.incomplete()
        self.schema(lmedia['schema'], rmedia['schema'], status, left_seen=left_seen, right_seen=right_seen)


def compare_response_contracts(previous, candidate, budget=None):
    """Return only safely observable differences, with explicit completeness."""
    budget = budget if budget is not None else {'nodes': 0, 'items': 0}
    state = _Comparison(previous, candidate, budget)
    if budget['nodes'] >= 20000 or budget['items'] >= 1000:
        return {'complete': False, 'items': []}
    if not isinstance(previous, Mapping) or not isinstance(candidate, Mapping):
        return {'complete': False, 'items': []}
    left, right = previous.get('responses'), candidate.get('responses')
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return {'complete': False, 'items': []}
    if len(left) > _MAX_STATUS or len(right) > _MAX_STATUS:
        return {'complete': False, 'items': []}
    def statuses(value):
        result = set()
        for key in value:
            if isinstance(key, str) and len(key) == 3 and key.isascii() and key.isdigit() and 100 <= int(key) <= 599:
                result.add(key)
            else:
                state.incomplete()
        return result
    left_status, right_status = statuses(left), statuses(right)
    if not left_status or not right_status or left_status != right_status:
        state.incomplete()
    for status in sorted(left_status - right_status):
        state.add('status_removed', status)
    for status in sorted(right_status - left_status):
        state.add('status_added', status)
    for status in sorted(left_status & right_status):
        state.response(left[status], right[status], status)
    return {'complete': state.complete, 'items': state.items}
