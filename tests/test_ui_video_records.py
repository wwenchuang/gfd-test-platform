"""Bounded parent-owned evidence, exact logical refs and snapshot file integrity."""
import json
import os
import stat
import pytest
from task_server.services import ui_video_records as records


def receipt(text='原文\n😀'):
    return {'content': text, 'providerId': 'official', 'model': 'qwen3.8-max',
            'fallbackUsed': False, 'finishReason': 'stop', 'usage': {'totalTokens': 2},
            'videoPolicy': None, 'gateway_request_sha256': 'a' * 64}


def test_roundtrip_160_entries_aggregates_and_snapshot(tmp_path):
    with records.RecordStore(tmp_path / 'records') as store:
        refs = [store.append('receipt', receipt(str(i) + '\n😀')) for i in range(160)]
        assert len(list((tmp_path / 'records').iterdir())) < 64
        assert [store.read(ref)['content'] for ref in refs] == [str(i) + '\n😀' for i in range(160)]
        aggregate = store.append('aggregate', {'refs': refs})
        assert store.read(aggregate) == {'refs': refs}
        snapshot = store.snapshot()
    with records.RecordStore.open_snapshot(tmp_path / 'records', snapshot) as reader:
        assert reader.read(refs[0]) == receipt('0\n😀')
        assert reader.read(aggregate) == {'refs': refs}
    assert stat.S_IMODE((tmp_path / 'records').stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (tmp_path / 'records').iterdir())


@pytest.mark.parametrize('case', ['entry', 'file', 'unknown', 'temp', 'symlink', 'duplicate'])
def test_corruption_and_unknown_entries_fail_closed(tmp_path, case):
    with records.RecordStore(tmp_path / 'records') as store:
        ref = store.append('receipt', receipt())
        if case == 'entry': ref = {**ref, 'sha256': 'b' * 64}
        if case == 'unknown': ref = {**ref, 'entry': 99}
        if case == 'file': next((tmp_path / 'records').iterdir()).write_text('{}')
        if case == 'temp': (tmp_path / 'records' / '.pending').write_text('')
        if case == 'symlink':
            p = next((tmp_path / 'records').iterdir()); p.unlink(); p.symlink_to(tmp_path / 'elsewhere')
        if case == 'duplicate':
            data = store.snapshot(); data['files'].append(data['files'][0])
            with pytest.raises(records.RecordError): records.RecordStore.open_snapshot(tmp_path / 'records', data)
            return
        with pytest.raises(records.RecordError): store.read(ref)
        with pytest.raises(records.RecordError): store.append('receipt', receipt('later'))


def test_limits_include_atomic_peak_and_latch(tmp_path):
    with records.RecordStore(tmp_path / 'records', max_total=1500, max_file=1200) as store:
        ref = store.append('receipt', receipt('x' * 450))
        before = next((tmp_path / 'records').iterdir()).read_bytes()
        with pytest.raises(records.RecordError, match='limit'):
            store.append('receipt', receipt('y' * 450))
        assert next((tmp_path / 'records').iterdir()).read_bytes() == before
        with pytest.raises(records.RecordError): store.read(ref)
    assert not any(p.name.startswith('.') for p in (tmp_path / 'records').iterdir())


def test_caps_cannot_be_raised_and_file_cap_includes_temp(tmp_path):
    with pytest.raises(records.RecordError): records.RecordStore(tmp_path / 'bad', max_files=65)
    with records.RecordStore(tmp_path / 'records', max_files=2, max_file=1100) as store:
        store.append('receipt', receipt('x' * 500))
        store.append('receipt', receipt('y' * 500))
        with pytest.raises(records.RecordError): store.append('receipt', receipt('z'))


def test_per_file_limit_symlink_ancestry_and_privacy(tmp_path):
    (tmp_path / 'real').mkdir(); (tmp_path / 'link').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(records.RecordError): records.RecordStore(tmp_path / 'link' / 'records')
    with records.RecordStore(tmp_path / 'records') as store:
        source = receipt(); source.update(reasoning_content='PRIVATE', actor_id='PRIVATE', images=['PRIVATE'])
        ref = store.append('receipt', source)
        assert store.read(ref) == receipt()
        assert 'PRIVATE' not in json.dumps(store.snapshot())
        with pytest.raises(records.RecordError): store.append('receipt', receipt('x' * (1024 * 1024)))


def test_output_truncated_code_survives_projection_and_snapshot_reopen(tmp_path):
    source = receipt('')
    source.update(http_status=500, rejection_code='video_stage_output_truncated')
    assert records.safe_record('receipt', source) == source
    with records.RecordStore(tmp_path / 'records') as store:
        ref = store.append('receipt', source)
        snapshot = store.snapshot()
    with records.RecordStore.open_snapshot(tmp_path / 'records', snapshot) as reader:
        assert reader.read(ref) == source


@pytest.mark.parametrize('code', [
    'unknown_PRIVATE_CODE', 'video_stage_output_truncated PRIVATE_CODE',
    {'error': 'PRIVATE_CODE'}, ['video_stage_output_truncated'], True, None,
])
def test_untrusted_rejection_codes_are_omitted_from_safe_and_durable_records(tmp_path, code):
    source = receipt('')
    source.update(http_status=500, rejection_code=code, reasoning_content='PRIVATE_REASONING')
    safe = records.safe_record('receipt', source)
    assert 'rejection_code' not in safe and safe['http_status'] == 500
    with records.RecordStore(tmp_path / 'records') as store:
        ref = store.append('receipt', source)
        snapshot = store.snapshot()
    with records.RecordStore.open_snapshot(tmp_path / 'records', snapshot) as reader:
        assert reader.read(ref) == safe
    assert all(b'PRIVATE' not in path.read_bytes() for path in (tmp_path / 'records').iterdir())


def test_atomic_failure_latches_and_removes_temp(tmp_path, monkeypatch):
    with records.RecordStore(tmp_path / 'records') as store:
        store.append('receipt', receipt())
        monkeypatch.setattr(records.os, 'replace', lambda *a, **kw: (_ for _ in ()).throw(OSError('PRIVATE')))
        with pytest.raises(records.RecordError, match='write_failed'): store.append('receipt', receipt())
        assert len(list((tmp_path / 'records').iterdir())) == 1
        with pytest.raises(records.RecordError): store.append('receipt', receipt())


def test_status_projection_and_entry_refs_for_terminal_aggregate(tmp_path):
    with records.RecordStore(tmp_path / 'records') as store:
        raw = store.append('receipt', receipt())
        value = {'index': 0, 'reserved': True, 'raw_received': True, 'receipt_persisted': True,
                 'admitted': False, 'terminal': True, 'cleanup': True, 'transport_status': 'receipt_received',
                 'events': ['reserved', 'raw_received', 'receipt_persisted', 'cleanup'], 'receipt_ref': raw,
                 'actor_id': 'PRIVATE', 'error': 'PRIVATE'}
        ref = store.append('status', value)
        saved = store.read(ref)
        assert saved['receipt_ref'] == raw and saved['events'] == value['events']
        assert 'PRIVATE' not in json.dumps(saved)
        aggregate = store.append('aggregate', {'refs': [raw, ref]})
        assert store.read(aggregate)['refs'] == [raw, ref]


def test_fixed_total_and_files_limits_and_mutated_old_snapshot(tmp_path):
    with records.RecordStore(tmp_path / 'records') as store:
        first = store.append('receipt', receipt('x' * 900000))
        snap = store.snapshot()
        count = 1
        with pytest.raises(records.RecordError, match='limit'):
            for i in range(64):
                store.append('receipt', receipt('x' * 900000)); count += 1
        assert 16 <= count <= 18
        assert sum(p.stat().st_size for p in (tmp_path / 'records').iterdir()) <= 16*1024*1024
    with pytest.raises(records.RecordError): records.RecordStore.open_snapshot(tmp_path / 'records', snap)
    with records.RecordStore(tmp_path / 'groups', max_file=700) as store:
        for i in range(64): store.append('receipt', receipt('x' * 180))
        assert len(list((tmp_path / 'groups').iterdir())) == 64
        with pytest.raises(records.RecordError): store.append('receipt', receipt('x' * 180))


def test_snapshot_fails_if_last_shard_changes_and_parent_thread_only(tmp_path):
    import threading
    with records.RecordStore(tmp_path / 'records') as store:
        store.append('receipt', receipt()); snapshot = store.snapshot()
        store.append('receipt', receipt('next'))
        with pytest.raises(records.RecordError): records.RecordStore.open_snapshot(tmp_path / 'records', snapshot)
        errors = []
        def foreign():
            try: store.append('receipt', receipt('wrong owner'))
            except records.RecordError as e: errors.append(e)
        thread = threading.Thread(target=foreign); thread.start(); thread.join()
        assert errors
        with pytest.raises(records.RecordError): store.append('receipt', receipt('latched'))


@pytest.mark.parametrize('location', ['manifest', 'file'])
def test_record_version_types_are_exact_not_bool(tmp_path, location):
    import hashlib
    with records.RecordStore(tmp_path / 'records') as store:
        store.append('receipt', receipt()); snapshot = store.snapshot()
    if location == 'manifest': snapshot['version'] = True
    else:
        path = tmp_path / 'records' / snapshot['files'][0]['name']
        data = json.loads(path.read_bytes()); data['version'] = True
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        path.write_bytes(raw); snapshot['files'][0].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(records.RecordError): records.RecordStore.open_snapshot(tmp_path / 'records', snapshot)
