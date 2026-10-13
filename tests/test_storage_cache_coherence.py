"""Invalidation must win over JSON reads already in flight."""
import concurrent.futures
import threading

import pytest

from task_server import storage


@pytest.mark.parametrize('clear_all', [False, True])
def test_inflight_old_reader_cannot_repopulate_after_invalidation(tmp_path, monkeypatch, clear_all):
    path = tmp_path / 'jobs.json'
    storage.write_json_file(path, [{'status': 'pending'}])
    read_finished, release = threading.Event(), threading.Event()
    original = storage.read_json_file

    def pause_old_read(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if snapshot == [{'status': 'pending'}]:
            read_finished.set()
            assert release.wait(5)
        return snapshot

    monkeypatch.setattr(storage, 'read_json_file', pause_old_read)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(storage.read_json_cached, path, 60, [])
        try:
            assert read_finished.wait(2)
            if clear_all:
                path.write_text('[{"status":"running"}]')
                storage.invalidate_json_cache()
            else:
                storage.write_json_file(path, [{'status': 'running'}])
            current = storage.read_json_cached(path, 60, [])
            assert current == [{'status': 'running'}]
        finally:
            release.set()
        # Preserve the original reader's own snapshot; only publishing it is forbidden.
        assert pending.result(timeout=2) == [{'status': 'pending'}]
    assert storage.read_json_cached(path, 60, []) is current


def test_unchanged_cache_preserves_identity_and_default(tmp_path):
    path = tmp_path / 'missing.json'
    storage.invalidate_json_cache(path)
    fallback = []
    assert storage.read_json_cached(path, 60, fallback) is fallback
    assert storage.read_json_cached(path, 60, []) is fallback
