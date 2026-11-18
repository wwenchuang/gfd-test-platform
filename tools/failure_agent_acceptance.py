#!/usr/bin/env python3
"""Local server operator acceptance against an existing authorized report.

Uses the server's existing configuration and Qwen transport. This script does not
start tests, devices, notifications, or write business state. --username identifies
a trusted local operator; do not expose this entrypoint as an HTTP request handler.
The selected report is read through EvidenceSource's current permission checks.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _actor(username):
    from task_server import identity
    from task_server.services.operation_attribution import actor_from_trusted_username
    profile = identity.get_access_profile(username)
    if not profile or profile.get('status') != 'active' or profile.get('must_change_password'):
        raise PermissionError('access_denied')
    actor = actor_from_trusted_username(username)
    if actor.get('kind') != 'user':
        raise PermissionError('access_denied')
    return actor


def run_real(args, cancelled):
    from task_server.services.failure_analysis_agent import investigate
    from task_server.services.failure_analysis_sources import EvidenceSource
    from task_server.services.operation_attribution import actor_context, current_actor
    actor = _actor(args.username)
    with actor_context(actor):
        source = EvidenceSource(current_actor(), args.source, args.run_id)
        source.authorize()
        result = investigate(source, cancelled=cancelled)
        source.authorize()
    return {'mode': 'real_report', 'evidence_origin': 'existing_platform_report',
            'business_execution_performed': False, 'source': args.source, 'run_id': args.run_id,
            'created_at': datetime.now(timezone.utc).isoformat(), 'analysis': result}


class _SyntheticSource:
    """Clearly labelled protocol fixture. Never reads or impersonates a real run."""
    def __init__(self, username, actor, variant):
        self.username, self.actor, self.variant = username, actor, variant

    def authorize(self):
        if _actor(self.username).get('user_id') != self.actor.get('user_id'):
            raise PermissionError('access_denied')

    def _evidence(self, kind, data):
        return {'evidence': [{'id': 'synthetic:' + self.variant + ':' + kind, 'kind': kind,
            'title': '[SYNTHETIC] ' + kind, 'href': None, 'data': dict(data, synthetic=True)}], 'missing': []}

    def get_failed_steps(self):
        self.authorize()
        result = self._evidence('failure', {'case_id': 'representative', 'page': '模型列表',
            'failed_step': '等待加载完成超时', 'assertion': '预期展示模型卡片，实际持续显示加载中',
            'description': 'This is a constructed protocol fixture, not a real UI execution.'})
        result['cases'] = [{'case_id': 'representative', 'name': '模型列表加载', 'status': 'FAILED'}]
        return result

    def get_request_trace(self, case_id):
        self.authorize()
        if case_id != 'representative':
            raise LookupError('case_not_found')
        response = ({'status_code': 502, 'body': {'code': 'UPSTREAM_UNAVAILABLE'}, 'request_id': 'synthetic-gateway-1'}
                    if self.variant == 'gateway_failure' else
                    {'status_code': 200, 'body': {'code': 0, 'data': {'items': [{'id': 'model-1'}]}},
                     'request_id': 'synthetic-payload-1'})
        return self._evidence('request', {'request': {'method': 'GET', 'path': '/models'},
            'response': response, 'recording_scope': 'same synthetic run and case'})

    def compare_last_pass(self, case_id):
        self.authorize()
        if case_id != 'representative':
            raise LookupError('case_not_found')
        if self.variant == 'gateway_failure':
            return {'evidence': [], 'missing': ['synthetic fixture has no comparable historical record'], 'comparable': False}
        result = self._evidence('comparison', {'identity_match': {'case_version': 'fixture-v1',
            'project': 'fixture-project', 'environment_revision': 'fixture-env-v1', 'source_revision': 'fixture-source-v1'},
            'history_is_earlier': True,
            'previous_response': {'status_code': 200, 'body': {'code': 0, 'data': {'list': [{'id': 'model-1'}]}}},
            'current_response': {'status_code': 200, 'body': {'code': 0, 'data': {'items': [{'id': 'model-1'}]}}}})
        result['comparable'] = True
        return result


def run_scenarios(args, cancelled):
    from task_server.services.failure_analysis_agent import investigate
    from task_server.services.operation_attribution import actor_context
    actor = _actor(args.username)
    scenarios = []
    with actor_context(actor):
        for variant in ('gateway_failure', 'response_shape_change'):
            result = investigate(_SyntheticSource(args.username, actor, variant), cancelled=cancelled)
            scenarios.append({'name': variant, 'analysis': result})
            if cancelled():
                break
    paths = [[entry['name'] for entry in item['analysis']['trace'] if entry['kind'] == 'tool'] for item in scenarios]
    return {'mode': 'synthetic_evidence_real_model', 'evidence_origin': 'constructed_protocol_fixtures',
            'business_execution_performed': False, 'created_at': datetime.now(timezone.utc).isoformat(),
            'different_tool_paths': len(paths) == 2 and paths[0] != paths[1], 'scenarios': scenarios}


def _scrub(value, secret=''):
    """EvidenceSource already redacts business data; additionally hide transport key."""
    if isinstance(value, dict):
        return {key: _scrub(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, secret) for item in value]
    if isinstance(value, str):
        value = value.replace(secret, '[已隐藏]') if secret else value
        return re.sub(r'(?i)\b(?:sk-[a-z0-9_-]+|Bearer\s+[^\s,;]+)', '[已隐藏]', value)
    return value


def _console(artifact):
    if 'scenarios' in artifact:
        return {'mode': artifact['mode'], 'different_tool_paths': artifact['different_tool_paths'],
                'scenarios': [dict(_console(item), name=item['name']) for item in artifact['scenarios']]}
    result = artifact.get('analysis') or {}
    return {'mode': artifact.get('mode'), 'status': result.get('status'),
            'stop_reason': result.get('stop_reason'), 'rounds': result.get('rounds', 0),
            'tools': [entry.get('name') for entry in result.get('trace', []) if entry.get('kind') == 'tool'],
            'summary': (result.get('result') or {}).get('summary', ''), 'usage': result.get('usage', {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--source', choices=('api', 'ui'))
    mode.add_argument('--protocol-scenarios', action='store_true', help='Constructed evidence with actual configured Qwen calls; not business acceptance')
    parser.add_argument('--run-id')
    parser.add_argument('--username', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.source) != bool(args.run_id):
        parser.error('--run-id is required only with --source')
    mode_name = 'synthetic_evidence_real_model' if args.protocol_scenarios else 'real_report'
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never follow a symlink or overwrite an earlier acceptance artifact.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        print(json.dumps({'status': 'failed', 'stop_reason': 'output_unavailable'}))
        return 3
    stopped = [False]
    old_handler = signal.signal(signal.SIGINT, lambda *_: stopped.__setitem__(0, True))
    try:
        try:
            run = run_scenarios if args.protocol_scenarios else run_real
            artifact = run(args, cancelled=lambda: stopped[0])
        except (PermissionError, LookupError):
            artifact = {'mode': mode_name, 'analysis': {'status': 'failed', 'stop_reason': 'access_denied'}}
        except Exception:
            artifact = {'mode': mode_name, 'analysis': {'status': 'failed', 'stop_reason': 'acceptance_unavailable'}}
        try:
            from task_server.config import dashscope_api_key
            key = dashscope_api_key(required=False)
        except Exception:
            key = ''
        artifact = _scrub(artifact, key)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            fd = None
            json.dump(artifact, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps(_console(artifact), ensure_ascii=False, allow_nan=False))
        statuses = [item['analysis']['status'] for item in artifact.get('scenarios', [])] or [artifact.get('analysis', {}).get('status')]
        status = 'completed' if all(status == 'completed' for status in statuses) else next(status for status in statuses if status != 'completed')
        return 0 if status == 'completed' else 2 if status == 'limited' else 130 if status == 'cancelled' else 3
    finally:
        signal.signal(signal.SIGINT, old_handler)
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
