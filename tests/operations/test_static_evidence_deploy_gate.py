"""Deployment must stop before side effects when static evidence checks fail."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_web_sync_stops_before_any_container_mutation_when_scan_rejects(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    log = tmp_path / 'docker.log'
    docker = fakebin / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_LOG"\nif [ "$1" = ps ]; then echo test-container; fi\n')
    docker.chmod(0o755)
    # The gate's interpreter rejects the source. No docker calls may result.
    python = fakebin / 'python3'
    python.write_text('#!/bin/sh\nexit 19\n')
    python.chmod(0o755)
    page = tmp_path / 'task-manager.html'
    page.write_text('<html></html>')
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'TEST_DOCKER_LOG': str(log),
           'SOURCE_HTML': str(page), 'CONTAINER': 'test-container'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/sync-docker-web.sh')], env=env, capture_output=True)
    assert result.returncode != 0
    assert not log.exists() or not log.read_text().strip()


def test_scanner_rejects_plain_gzip_and_unknown_brotli_without_echoing(tmp_path, monkeypatch, capsys):
    import gzip
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location('evidence_guard', ROOT / 'deploy/static_evidence_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = b'11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(module, 'SIGNATURE_DIGESTS', {hashlib.sha256(token).digest()})
    source = tmp_path / 'static'
    source.mkdir()
    path = source / 'old.js'
    path.write_bytes(b'var evidence="' + token + b'"')
    assert module.main([str(source)]) != 0
    assert token.decode() not in capsys.readouterr().out
    path.unlink()
    (source / 'old.js.gz').write_bytes(gzip.compress(token))
    assert module.main([str(source)]) != 0
    (source / 'old.js.gz').unlink()
    (source / 'unknown.js.br').write_bytes(b'not decoded')
    assert module.main([str(source)]) != 0


def test_scanner_accepts_safe_data_and_rejects_symlinks(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('evidence_guard', ROOT / 'deploy/static_evidence_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'static'
    source.mkdir()
    (source / 'safe.js').write_bytes(b'console.log("safe")')
    assert module.main([str(source)]) == 0
    (source / 'escape.js').symlink_to(source / 'safe.js')
    assert module.main([str(source)]) != 0


def test_installer_stops_before_package_install_when_scan_rejects(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    marker = tmp_path / 'mutations.log'
    fakeid = fakebin / 'id'
    fakeid.write_text('#!/bin/sh\necho 0\n')
    fakeid.chmod(0o755)
    python = fakebin / 'python3'
    python.write_text('#!/bin/sh\nexit 19\n')
    python.chmod(0o755)
    for name in ('apt-get', 'yum', 'install', 'systemctl', 'docker'):
        target = fakebin / name
        target.write_text('#!/bin/sh\nprintf called >> "$TEST_MUTATIONS"\n')
        target.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'TEST_MUTATIONS': str(marker)}
    result = subprocess.run(['bash', str(ROOT / 'deploy/install-server.sh')], env=env, capture_output=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_scanner_rejects_signature_in_filename_and_malformed_gzip(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location('evidence_guard', ROOT / 'deploy/static_evidence_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = b'11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(module, 'SIGNATURE_DIGESTS', {hashlib.sha256(token).digest()})
    source = tmp_path / 'static'
    source.mkdir()
    named = source / (token.decode() + '.js')
    named.write_text('safe content')
    assert module.main([str(source)]) != 0
    named.unlink()
    (source / 'corrupted.js.gz').write_bytes(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03garbage')
    assert module.main([str(source)]) != 0


def test_scanner_detects_known_uuid_overlapping_another_uuid(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location('evidence_guard', ROOT / 'deploy/static_evidence_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = b'11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(module, 'SIGNATURE_DIGESTS', {hashlib.sha256(token).digest()})
    # The first UUID occupies bytes 0..35; the known UUID starts at byte 28.
    prefix = b'aaaaaaaa-aaaa-aaaa-aaaa-aaaa'
    source = tmp_path / 'overlap.js'
    source.write_bytes(prefix + token)
    assert module.main([str(source)]) != 0
