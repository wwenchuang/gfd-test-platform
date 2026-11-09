#!/usr/bin/env python3
"""Read-only, conservative superset of enabled schedule cleanup versions.

Access scopes and in-flight snapshots are intentionally not resolved.
"""
import json
import os
import sys
from uuid import UUID

try:
    from scripts.audit_cleanup_compatibility import classify
except ModuleNotFoundError:
    from audit_cleanup_compatibility import classify


def resolve(jobs, tasks, baselines, versions):
    result = {}
    for job in jobs:
        job_id, project, kind, targets = (job.get(k) for k in ('id', 'project_id', 'target_type', 'targets'))
        if not job_id or not project or not isinstance(targets, list) or not targets:
            raise ValueError('incomplete schedule')
        for baseline in baselines.values():
            if baseline['project_id'] == project and baseline['status'] == 'active':
                version = versions.get(baseline['version_id'])
                if version is None or version['project_id'] != project:
                    raise ValueError('invalid active baseline version')
        if kind == 'cases':
            roots = list(targets)
        elif kind == 'baselines':
            if any(t not in baselines or baselines[t]['project_id'] != project or baselines[t]['status'] != 'active' for t in targets):
                raise ValueError('missing or invalid baseline')
            roots = [baselines[t]['version_id'] for t in targets]
        elif kind == 'baseline_group':
            roots = [b['version_id'] for b in baselines.values() if b['project_id'] == project and b['status'] == 'active' and b['group_name'] in targets]
        elif kind == 'task':
            if len(targets) != 1 or targets[0] not in tasks:
                raise ValueError('missing task')
            task = tasks[targets[0]]
            if task['project_id'] != project or not isinstance(task['selected_endpoint_ids'], list):
                raise ValueError('invalid task')
            roots = [b['version_id'] for b in baselines.values() if b['project_id'] == project and b['status'] == 'active'
                     and b['version_id'] in versions and versions[b['version_id']]['endpoint_id'] in task['selected_endpoint_ids']]
        else:
            raise ValueError('unknown target')
        if not roots:
            raise ValueError('empty target')
        visited, visiting = set(), set()

        def visit(identifier):
            if identifier in visited:
                return
            if identifier in visiting or identifier not in versions:
                raise ValueError('missing or cyclic dependency')
            version = versions[identifier]
            if version['project_id'] != project:
                raise ValueError('cross-project version')
            spec = version.get('dependency_spec') or {}
            if not isinstance(spec, dict) or not isinstance(spec.get('dependencies', []), list):
                raise ValueError('invalid dependencies')
            visiting.add(identifier)
            for dep in spec.get('dependencies', []):
                if not isinstance(dep, dict) or not isinstance(dep.get('case_version_id'), str) or not dep['case_version_id'] or not isinstance(dep.get('exports', []), list):
                    raise ValueError('invalid dependency')
                visit(dep['case_version_id'])
            visiting.remove(identifier)
            visited.add(identifier)
            if len(visited) > 10000:
                raise ValueError('dependency limit exceeded')

        for identifier in roots:
            visit(identifier)
        result[job_id] = visited
    return result


def inventory(connection):
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        jobs_by_id = {}
        for job_id, project, kind, target_kind, target in connection.execute(
            'SELECT j.id, j.project_id, j.target_type, t.target_type, t.target_id '
            'FROM api_scheduled_jobs j LEFT JOIN api_scheduled_job_targets t ON t.job_id=j.id '
            'WHERE j.enabled ORDER BY j.id, t.sequence'):
            job = jobs_by_id.setdefault(job_id, {'id': job_id, 'project_id': project, 'target_type': kind, 'targets': []})
            if target is None or target_kind != kind:
                raise ValueError('invalid schedule target')
            job['targets'].append(target)
        tasks = {i: {'project_id': p, 'selected_endpoint_ids': e} for i,p,e in connection.execute(
            'SELECT id, project_id, selected_endpoint_ids FROM api_test_tasks')}
        baselines = {i: {'project_id': p, 'version_id': v, 'status': s, 'group_name': g} for i,p,v,s,g in connection.execute(
            'SELECT id, project_id, case_version_id, status, group_name FROM api_baselines')}
        versions = {i: {'project_id': p, 'endpoint_id': e, 'dependency_spec': d, 'processing_spec': s} for i,p,e,d,s in connection.execute(
            'SELECT v.id, c.project_id, v.endpoint_id, v.dependency_spec, v.processing_spec FROM api_case_versions v LEFT JOIN api_cases c ON c.id=v.case_id')}
        resolved = resolve(list(jobs_by_id.values()), tasks, baselines, versions)
        data = []
        for job_id, version_ids in sorted(resolved.items()):
            data.append({'job_id': str(UUID(str(job_id))), 'versions': [
                {'case_version_id': str(UUID(str(v))), **classify(versions[v]['processing_spec'])} for v in sorted(version_ids)]})
    return {'scope': 'enabled_schedules_access_unfiltered_superset', 'permissions_resolved': False,
            'inflight_snapshots_included': False, 'schedule_count': len(data), 'schedules': data}


def main():
    try:
        import psycopg
        dsn = os.environ['API_TESTING_DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            data = inventory(connection)
    except Exception:
        print('盘点未完成：请检查连接、权限、数据完整性与超时。', file=sys.stderr)
        return 1
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
