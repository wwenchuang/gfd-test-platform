"""Publish curated 3D assets without overwriting other workbench changes."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, wait

from task_server.services import job_service


def test_legacy_and_current_metadata_updates_read_after_shared_lock(tmp_path, monkeypatch):
    from task_server.storage import file_mutation_lock
    meta = tmp_path / 'meta.json'
    meta.write_text('{}')
    monkeypatch.setattr(job_service, 'TASK_META_FILE', str(meta))
    writers = [job_service._update_task_meta, job_service.update_task_meta]
    with ThreadPoolExecutor(2) as pool:
        ready = [threading.Event() for _ in writers]

        def update(index):
            ready[index].set()
            return writers[index]('m', f'{index}.yaml', {'remark': index})

        with file_mutation_lock(meta):
            futures = [pool.submit(update, i) for i in range(2)]
            assert all(event.wait(2) for event in ready)
            assert not wait(futures, timeout=0.1).done
            meta.write_text(json.dumps({'external': {'status': 'review'}}))
        for future in futures:
            future.result()
    result = json.loads(meta.read_text())
    assert result['external'] == {'status': 'review'}
    assert [result[f'm::{i}.yaml']['remark'] for i in range(2)] == [0, 1]


def test_checked_promotion_is_idempotent_and_rejects_drift(tmp_path, monkeypatch):
    import hashlib
    import runpy
    from pathlib import Path

    import pytest
    from task_server import config
    from task_server.services import case_service, yaml_service
    from task_server.services import ui_execution_contract

    root = Path(__file__).resolve().parents[1]
    publisher = runpy.run_path(str(root / 'deploy/3d-daily/publish.py'))
    daily = runpy.run_path(str(root / 'deploy/3d-daily/run.py'))
    actor = {'kind': 'user', 'username': 'tester', 'user_id': '42', 'display_name': 'Tester'}
    daily['trusted_actor'] = lambda: actor
    monkeypatch.setattr(ui_execution_contract.identity, 'get_access_profile', lambda name: {
        **actor, 'status': 'active', 'permissions': ['ui.view', 'ui.execute', 'ui.baseline'],
        'scope': {'ui_apps': ['com.kfb.model']}})
    runtime, source = tmp_path / 'runtime', tmp_path / 'source'
    module = '3D打印基线'
    for tree in (runtime, source):
        (tree / module).mkdir(parents=True)
    for target in (config, case_service, yaml_service):
        monkeypatch.setattr(target, 'TASK_DIR', str(runtime))
    monkeypatch.setattr(yaml_service, 'VERSION_DIR', str(tmp_path / 'versions'))
    monkeypatch.setattr(case_service, '_candidate_task_roots', lambda: [str(runtime)])
    monkeypatch.setenv('TASK_ASSET_DB', str(tmp_path / 'private' / 'assets.sqlite3'))
    meta_file = tmp_path / 'meta.json'
    monkeypatch.setattr(job_service, 'TASK_META_FILE', str(meta_file))
    meta = {'keep': {'scope': ['unchanged']}}
    previous, desired = [], []
    for filename in [*daily['SMOKE'], daily['DRAGON'], '新增功能.yaml']:
        is_new = filename == '新增功能.yaml'
        title = Path(filename).stem
        cid = yaml_service.stable_case_id('com.kfb.model', module, filename, title)
        data = (f'android: {{}}\ntasks:\n- name: {title}\n  # baseline.case_id: {cid}\n  flow:\n'
                '  - launch: com.kfb.model\n  - aiAssert: 页面显示我的设备\n')
        row = {'case_id': cid, 'module': module, 'filename': filename,
               'sha256': hashlib.sha256(data.encode()).hexdigest()}
        if not is_new:
            previous.append(row.copy())
            (runtime / module / filename).write_text(data)
            meta[job_service.task_key(module, filename)] = {
                'module': module, 'file': filename, 'status': 'active', 'note': 'manual'}
        if filename == daily['DRAGON']:
            data = data.replace('我的设备', '我的设备和打印记录')
            row['sha256'] = hashlib.sha256(data.encode()).hexdigest()
        desired.append(row)
        (source / module / filename).write_text(data)
    meta_file.write_text(json.dumps(meta))
    publish = publisher['publish']
    new_file = runtime / module / '新增功能.yaml'
    dragon = runtime / module / daily['DRAGON']
    original = dragon.read_bytes()
    publish(previous, desired, source, daily, False)
    assert not new_file.exists() and dragon.read_bytes() == original

    def reject(rows):
        raise ValueError('failed after adding metadata')

    with pytest.raises(ValueError, match='failed after'):
        publish(previous, desired, source, dict(daily, validate_published=reject), True)
    assert not new_file.exists() and dragon.read_bytes() == original
    assert json.loads(meta_file.read_text()) == meta
    dragon.write_bytes(original + b'# online change\n')
    with pytest.raises(ValueError, match='live_.*changed'):
        publish(previous, desired, source, daily, True)
    assert not new_file.exists() and json.loads(meta_file.read_text()) == meta
    dragon.write_bytes(original)
    publish(previous, desired, source, daily, True)
    applied_meta = json.loads(meta_file.read_text())
    added = applied_meta.pop(job_service.task_key(module, new_file.name))
    assert applied_meta == meta and added['status'] == 'baseline' and added['status_updated_by'] == 'tester'
    timestamps = [p.stat().st_mtime_ns for p in (new_file, dragon, meta_file)]
    publish(previous, desired, source, daily, True)
    assert [p.stat().st_mtime_ns for p in (new_file, dragon, meta_file)] == timestamps
