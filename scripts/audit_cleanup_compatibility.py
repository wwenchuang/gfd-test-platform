#!/usr/bin/env python3
"""Read-only structural inventory, conservatively covering scheduled projects."""

import json
import os
import sys
from uuid import UUID

# Both active baselines and enabled schedules may run same-project dependency
# versions. Include ALL versions in either kind of project rather than trying
# to infer the graph from the direct target alone.
SCOPE_INTEGRITY_SQL = """
SELECT EXISTS (
    SELECT 1 FROM api_cases AS c WHERE c.project_id IS NULL
) OR EXISTS (
    SELECT 1 FROM api_case_versions AS v
    LEFT JOIN api_cases AS c ON c.id = v.case_id WHERE c.id IS NULL
) OR EXISTS (
    SELECT 1 FROM api_baselines AS b
    LEFT JOIN api_case_versions AS v ON v.id = b.case_version_id
    LEFT JOIN api_cases AS c ON c.id = v.case_id
    WHERE b.status = 'active' AND (
        b.project_id IS NULL OR v.id IS NULL OR c.id IS NULL
        OR c.project_id IS DISTINCT FROM b.project_id
    )
) OR EXISTS (
    SELECT 1 FROM api_scheduled_jobs AS j WHERE j.enabled AND j.project_id IS NULL
)
"""
INVENTORY_SQL = """
SELECT v.id, v.processing_spec
FROM api_case_versions AS v
JOIN api_cases AS c ON c.id = v.case_id
WHERE EXISTS (
    SELECT 1 FROM api_baselines AS b
    WHERE b.project_id = c.project_id AND b.status = 'active'
) OR EXISTS (
    SELECT 1 FROM api_scheduled_jobs AS j
    WHERE j.project_id = c.project_id AND j.enabled
)
ORDER BY v.id
"""


def classify(spec):
    result = dict.fromkeys(('multi_cleanup', 'earlier_output', 'optional_or_default',
                            'conditional', 'pre_alias', 'polling', 'indeterminate'), False)
    if not isinstance(spec, dict):
        result['indeterminate'] = True
        return result
    steps, pre = spec.get('cleanup_steps', []), spec.get('pre', [])
    if not isinstance(steps, list) or not isinstance(pre, list):
        result['indeterminate'] = True
        return result
    enabled = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('enabled', True), bool):
            result['indeterminate'] = True
            continue
        if step.get('enabled', True):
            enabled.append(step)
    result['multi_cleanup'] = len(enabled) > 1
    for index, step in enumerate(enabled):
        extractions = step.get('extractions', [])
        if not isinstance(extractions, list) or any(not isinstance(item, dict) for item in extractions):
            result['indeterminate'] = True
            continue
        if index < len(enabled) - 1 and extractions:
            result['earlier_output'] = True
        for item in extractions:
            required = item.get('required', True)
            if not isinstance(required, bool):
                result['indeterminate'] = True
            if required is False or item.get('default') is not None:
                result['optional_or_default'] = True
        if step.get('only_if_variable'):
            result['conditional'] = True
        polling = step.get('polling')
        if polling is not None:
            if not isinstance(polling, dict):
                result['indeterminate'] = True
            else:
                attempts = polling.get('max_attempts', 1)
                if not isinstance(attempts, int) or isinstance(attempts, bool):
                    result['indeterminate'] = True
                elif attempts > 1:
                    result['polling'] = True
    for action in pre:
        if not isinstance(action, dict):
            result['indeterminate'] = True
        elif action.get('action') in {'copy_variable', 'json_encode', 'json_decode'}:
            result['pre_alias'] = True
    return result


def inventory(connection):
    """Return identifiers and flags only, without printing database content."""
    rows = []
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        if connection.execute(SCOPE_INTEGRITY_SQL).fetchone()[0]:
            raise ValueError('project scope cannot be determined')
        with connection.cursor(name='cleanup_inventory_readonly') as cursor:
            cursor.execute(INVENTORY_SQL)
            for identifier, processing in cursor:
                # Reject malformed IDs instead of leaking arbitrary database text.
                case_version_id = str(UUID(str(identifier)))
                rows.append({'case_version_id': case_version_id, **classify(processing)})
    return {'scope': 'all_versions_in_active_baseline_or_enabled_schedule_projects',
            'exact_schedule_membership_resolved': False,
            'count': len(rows), 'versions': rows}


def main():
    try:
        import psycopg
        dsn = os.environ['API_TESTING_DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            result = inventory(connection)
    except Exception:
        # Database exceptions can embed DSNs or SQL values. Keep stderr generic.
        print('盘点未完成：请检查数据库连接、权限、结构和超时设置。', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
