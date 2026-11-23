"""Check or publish curated daily YAMLs under the existing workbench identity."""
import argparse
import hashlib
import json
import runpy
from pathlib import Path

from task_server import config
from task_server.services import asset_lineage, case_service, job_service, yaml_service
from task_server.services.operation_attribution import actor_context, actor_from_trusted_username
from task_server.services.ui_execution_contract import require_ui_actor
from task_server.services.yaml_static_validator import validate_yaml_static_executable
from task_server.storage import clean_filename, write_text_file


APP = 'com.kfb.model'
PUBLISHED = {'active', 'baseline'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def location(row):
    module, filename = row['module'], row['filename']
    if (module not in {'3D打印基线', 'AI测试'} or clean_filename(filename) != filename
            or filename.startswith('.') or Path(filename).name != filename):
        raise ValueError('invalid_case_path')
    return module, filename


def prepare(previous, desired, source_root, catalog, metadata):
    """Check every revision and every new metadata key before touching any asset."""
    old = {row['case_id']: row for row in previous}
    if not old.keys() <= {row['case_id'] for row in desired}:
        raise ValueError('existing_case_removed')
    indexed = {asset['case_id']: asset for asset in catalog if asset.get('case_id')}
    by_path = {(a['module'], a['file']): a for a in catalog if a.get('file')}
    writes, additions = [], {}
    for row in desired:
        cid = row['case_id']
        module, filename = location(row)
        prior = old.get(cid)
        if prior and location(prior) != (module, filename):
            raise ValueError('existing_case_moved:' + cid)
        key = job_service.task_key(module, filename)
        new_asset = not prior
        path = Path(config.TASK_DIR).absolute() / module / filename
        if path.resolve() != path or not path.parent.is_dir():
            raise ValueError('invalid_runtime_path:' + filename)
        source = (source_root / module / filename).read_bytes()
        text = source.decode('utf-8')
        tasks = yaml_service.list_yaml_task_blocks(text)
        if len(tasks) != 1:
            raise ValueError('expected_one_task:' + filename)
        task = tasks[0]
        published = yaml_service.yaml_with_single_task(text, task['name'], APP)
        if (sha(source) != row['sha256'] or published != text
                or yaml_service.extract_baseline_meta_from_block(task['block']).get('case_id') != cid
                or yaml_service.resolve_app_package(module, filename, text) != APP
                or (new_asset and yaml_service.stable_case_id(APP, module, filename, task['name']) != cid)):
            raise ValueError('source_revision_or_identity_invalid:' + cid)
        if not validate_yaml_static_executable(published, strict=True)['ok']:
            raise ValueError('invalid_yaml:' + cid)
        asset = indexed.get(cid)
        collision = by_path.get((module, filename))
        if ((asset and (asset['module'], asset['file'], asset['app_package']) != (module, filename, APP))
                or (collision and collision.get('case_id') != cid)):
            raise ValueError('case_identity_collision:' + cid)
        before = path.read_bytes() if path.exists() else None
        allowed = {row['sha256']}
        if prior:
            allowed.add(prior['sha256'])
        if asset and sha(case_service.task_case_yaml(asset).encode()) not in allowed:
            raise ValueError('live_case_changed:' + cid)
        if before is not None and sha(before) not in allowed:
            raise ValueError('live_file_changed:' + cid)
        if prior and (not asset or asset['status'] not in PUBLISHED):
            raise ValueError('existing_case_unpublished:' + cid)
        if new_asset:
            if key in metadata:
                meta = metadata[key]
                if (not asset or meta.get('status') not in PUBLISHED
                        or meta.get('app_package', APP) != APP):
                    raise ValueError('existing_metadata_conflict:' + cid)
            else:
                additions[key] = {'module': module, 'file': filename, 'app_package': APP, 'status': 'baseline'}
        if before != source and (new_asset or prior['sha256'] != row['sha256']):
            writes.append((path, before, text))
    return writes, additions


def publish(previous, desired, source_root, daily, apply, publisher=None):
    daily['plan'](previous)
    daily['plan'](desired)
    actor = daily['trusted_actor']()
    with actor_context(actor):
        require_ui_actor(permission='ui.view', app_id=APP)
        require_ui_actor(permission='ui.execute', app_id=APP)
    with actor_context(actor_from_trusted_username(publisher) if publisher else actor):
        profile = require_ui_actor(permission='ui.baseline', app_id=APP)
        # Match runtime lock order used by the draft publisher. All shared metadata
        # writers must be running the version with locked_task_meta before applying.
        with asset_lineage.mutation_lock(), job_service.locked_task_meta() as metadata:
            writes, additions = prepare(previous, desired, source_root,
                                        case_service.list_task_case_assets(), metadata)
            print(f'{len(desired)} checked, {len(writes)} YAML writes, {len(additions)} new published rows')
            if not apply:
                return
            for path, before, _ in writes:
                if before is not None and not yaml_service.save_file_version(
                        path.parent.name, path.name, before.decode(), reason='daily-3d-publish'):
                    raise OSError('case_backup_failed:' + path.name)
            started = []
            try:
                for path, before, new in writes:
                    started.append((path, before))
                    write_text_file(str(path), new)
                    if path.read_bytes() != new.encode():
                        raise OSError('case_write_failed:' + path.name)
                if additions:
                    from datetime import datetime
                    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    patch = {key: dict(row, status_updated_by=profile['username'], updated_at=now)
                             for key, row in additions.items()}
                    job_service.save_task_meta({**metadata, **patch})
                daily['validate_published'](desired)
            except BaseException:
                if additions:
                    job_service.save_task_meta(metadata)
                for path, before in reversed(started):
                    if before is not None:
                        write_text_file(str(path), before.decode())
                    elif path.exists():
                        asset_lineage.delete_file(str(path))
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', required=True, type=Path, help='known pre-release manifest')
    parser.add_argument('--live', type=Path, default=Path('/opt/midscene-3d-daily/current/baselines.json'))
    parser.add_argument('--source-root', type=Path, required=True, help='candidate YAML tree grouped by module')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publisher', help='existing authorized publisher, defaults to the schedule owner')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    desired = json.loads((directory / 'baselines.json').read_text())
    previous = json.loads(args.previous.read_text())
    if json.loads(args.live.read_text()) not in (previous, desired):
        raise ValueError('daily_manifest_changed')
    publish(previous, desired, args.source_root, runpy.run_path(str(directory / 'run.py')), args.apply, args.publisher)


if __name__ == '__main__':
    main()
