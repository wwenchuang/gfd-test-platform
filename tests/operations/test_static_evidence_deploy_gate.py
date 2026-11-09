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


def test_updater_rejects_before_sonic_service_or_build_side_effects(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    marker = tmp_path / 'mutations.log'
    git = fakebin / 'git'
    git.write_text('#!/bin/sh\nif [ "$1" = rev-parse ]; then echo revision; fi\nexit 0\n')
    git.chmod(0o755)
    curl = fakebin / 'curl'
    curl.write_text('#!/bin/sh\nexit 0\n')
    curl.chmod(0o755)
    python = fakebin / 'python3'
    python.write_text('#!/bin/sh\nexit 19\n')
    python.chmod(0o755)
    for name in ('npm', 'docker', 'systemctl', 'sudo'):
        target = fakebin / name
        target.write_text('#!/bin/sh\nprintf called >> "$TEST_MUTATIONS"\n')
        target.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'SOURCE_DIR': str(source),
           'TEST_MUTATIONS': str(marker), 'BUILD_API_TEST': '1'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/update-main-server.sh')], env=env, capture_output=True)
    assert result.returncode != 0
    assert not marker.exists()


def test_scanner_rejects_compressed_archive_even_with_unexpected_extension(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location('evidence_guard', ROOT / 'deploy/static_evidence_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = b'11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(module, 'SIGNATURE_DIGESTS', {hashlib.sha256(token).digest()})
    archive = tmp_path / 'old.js'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
        stream.writestr('bundled.js', token)
    assert module.main([str(archive)]) != 0


def test_updater_checks_again_after_build_before_service_changes(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    marker = tmp_path / 'mutations.log'
    count = tmp_path / 'scan-count'
    for name, body in {
        'git': 'if [ "$1" = rev-parse ]; then echo revision; fi; exit 0',
        'curl': 'exit 0',
        'python3': 'if [ -f "$TEST_COUNT" ]; then exit 19; else touch "$TEST_COUNT"; exit 0; fi',
        'npm': 'exit 0',
        'docker': 'printf docker >> "$TEST_MUTATIONS"',
        'systemctl': 'printf systemctl >> "$TEST_MUTATIONS"',
        'sudo': 'printf sudo >> "$TEST_MUTATIONS"',
    }.items():
        path = fakebin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'SOURCE_DIR': str(source),
           'TEST_MUTATIONS': str(marker), 'TEST_COUNT': str(count), 'BUILD_API_TEST': '1'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/update-main-server.sh')], env=env, capture_output=True)
    assert result.returncode != 0
    assert count.exists()
    assert not marker.exists()


def test_installer_guard_rejection_does_not_trigger_service_restore(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    (source / 'deploy').mkdir()
    (source / 'deploy' / 'install-server.sh').write_text('#!/bin/sh\nexit 79\n')
    log = tmp_path / 'commands.log'
    for name, body in {
        'git': 'if [ "$1" = rev-parse ]; then echo revision; fi; exit 0',
        'id': 'echo 0', 'curl': 'exit 0', 'python3': 'exit 0',
        'docker': 'exit 0', 'systemctl': 'printf "%s\\n" "$*" >> "$TEST_LOG"; exit 0',
        'pm2': 'exit 0',
    }.items():
        path = fakebin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'SOURCE_DIR': str(source),
           'TEST_LOG': str(log), 'CONFIGURE_SONIC_RESTART': '0', 'SYNC_SONIC_RECORDER_HOOK': '0'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/update-main-server.sh')], env=env, capture_output=True)
    assert result.returncode == 79
    assert not log.exists() or 'start midscene-task.service' not in log.read_text()


def test_packager_stops_before_output_mutation_on_rejection(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    python = fakebin / 'python3'
    python.write_text('#!/bin/sh\nexit 19\n')
    python.chmod(0o755)
    output = tmp_path / 'output'
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'OUT_DIR': str(output)}
    result = subprocess.run(['bash', str(ROOT / 'deploy/package-server.sh')], env=env, capture_output=True)
    assert result.returncode != 0
    assert not output.exists()


def test_installer_reserves_guard_exit_code_for_preflight_only(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    for name, body in {'id': 'echo 0', 'getent': 'exit 0', 'python3': 'exit 0', 'install': 'exit 79'}.items():
        path = fakebin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/install-server.sh')], env=env, capture_output=True)
    assert result.returncode == 80


def test_updater_keeps_restore_for_real_install_failure(tmp_path):
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    (source / 'deploy').mkdir()
    (source / 'deploy' / 'install-server.sh').write_text('#!/bin/sh\nexit 80\n')
    log = tmp_path / 'commands.log'
    for name, body in {
        'git': 'if [ "$1" = rev-parse ]; then echo revision; fi; exit 0',
        'id': 'echo 0', 'curl': 'exit 0', 'python3': 'exit 0',
        'docker': 'exit 0', 'systemctl': 'printf "%s\\n" "$*" >> "$TEST_LOG"; exit 0',
        'pm2': 'exit 0',
    }.items():
        path = fakebin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    env = {**os.environ, 'PATH': f'{fakebin}:{os.environ["PATH"]}', 'SOURCE_DIR': str(source),
           'TEST_LOG': str(log), 'CONFIGURE_SONIC_RESTART': '0', 'SYNC_SONIC_RECORDER_HOOK': '0'}
    result = subprocess.run(['bash', str(ROOT / 'deploy/update-main-server.sh')], env=env, capture_output=True)
    assert result.returncode == 80
    assert 'start midscene-task.service' in log.read_text()
