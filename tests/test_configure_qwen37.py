"""Optional configuration migration; all files and secret sentinels are fixtures."""
import importlib
import json
import os
from pathlib import Path
import stat

import pytest


def tool():
    return importlib.import_module('deploy.configure_qwen37')


@pytest.fixture
def files(tmp_path):
    env = tmp_path / 'midscene.env'
    env.write_text("# fixture\nexport DASHSCOPE_MODEL='qwen3.8-max'\nDASHSCOPE_VL_MODEL=qwen-vl-max\nMIDSCENE_MODEL_NAME=\"qwen3.8-max\"\nDASHSCOPE_API_KEY='private-key-fixture'\nOTHER_MODEL=qwen3.8-max\n")
    providers = tmp_path / 'providers.json'
    providers.write_text(json.dumps({'providers': {
        'qwen_plus': {'name':'千问 Qwen 3.8 Max', 'model':'qwen3.8-max', 'apiKey':'private-provider-key-fixture'},
        'custom': {'name':'保留自定义通道', 'model':'other-model'},
        'similar': {'name':'保留近似名称', 'model':'qwen3.8-max-2026'},
    }}, ensure_ascii=False, indent=2) + '\n')
    env.chmod(0o600)
    providers.chmod(0o640)
    return env, providers


def invoke(files, *extra):
    env, providers = files
    return tool().main(['--env-file', str(env), '--providers-file', str(providers), *extra])


def test_default_is_dryrun_without_backup_or_mutation(files, capsys):
    before = [path.read_bytes() for path in files]
    assert invoke(files) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'dry_run'
    assert result['changes'] == {'env':2, 'providers':1}
    assert [path.read_bytes() for path in files] == before
    assert len(list(files[0].parent.iterdir())) == 2
    assert 'private-' not in json.dumps(result)


def test_apply_exact_fields_preserves_metadata_and_private_backups(files, capsys):
    originals = [path.read_bytes() for path in files]
    metadata = [(path.stat().st_uid,path.stat().st_gid,stat.S_IMODE(path.stat().st_mode)) for path in files]
    assert invoke(files, '--apply') == 0
    output = capsys.readouterr().out
    assert 'private-' not in output
    result = json.loads(output)
    assert result['status'] == 'applied'
    assert result['changes'] == {'env':2,'providers':1}
    env = files[0].read_text()
    assert "export DASHSCOPE_MODEL='qwen3.7-plus'" in env
    assert 'MIDSCENE_MODEL_NAME="qwen3.7-plus"' in env
    assert 'DASHSCOPE_VL_MODEL=qwen-vl-max' in env
    assert 'OTHER_MODEL=qwen3.8-max' in env
    assert 'private-key-fixture' in env
    providers = json.loads(files[1].read_text())['providers']
    assert providers['qwen_plus']['model'] == 'qwen3.7-plus'
    assert providers['qwen_plus']['name'] == '千问 Qwen 3.7 Plus'
    assert providers['qwen_plus']['apiKey'] == 'private-provider-key-fixture'
    assert providers['similar']['model'] == 'qwen3.8-max-2026'
    assert providers['custom']['model'] == 'other-model'
    assert [(p.stat().st_uid,p.stat().st_gid,stat.S_IMODE(p.stat().st_mode)) for p in files] == metadata
    for role, original, (uid,gid,_) in zip(('env','providers'), originals, metadata):
        backup = Path(result['backups'][role])
        assert backup.read_bytes() == original
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert (backup.stat().st_uid, backup.stat().st_gid) == (uid,gid)
    names = set(files[0].parent.iterdir())
    assert invoke(files, '--apply') == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'unchanged'
    assert set(files[0].parent.iterdir()) == names


@pytest.mark.parametrize('broken', ['json','env','duplicate_json','duplicate_env','missing'])
def test_all_validation_completes_before_any_change(files, broken, capsys):
    if broken == 'json': files[1].write_text('{private-provider-key-fixture')
    elif broken == 'env': files[0].write_text("DASHSCOPE_API_KEY='private-key-fixture\nDASHSCOPE_MODEL=qwen3.8-max\n")
    elif broken == 'duplicate_json': files[1].write_text('{"providers":{},"providers":{"qwen":{"model":"qwen3.8-max"}}}')
    elif broken == 'duplicate_env': files[0].write_text('DASHSCOPE_MODEL=qwen3.8-max\nDASHSCOPE_MODEL=qwen3.8-max\n')
    else: files[1].unlink()
    before = {path:path.read_bytes() for path in files if path.exists()}
    assert invoke(files, '--apply') != 0
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'error'
    assert 'private-' not in output
    assert {path:path.read_bytes() for path in before} == before
    assert len(list(files[0].parent.iterdir())) == len(before)


def test_symlink_is_rejected_without_changing_target(files, capsys):
    env, providers = files
    original = env.read_bytes()
    target = env.with_name('actual.env')
    env.rename(target)
    env.symlink_to(target)
    assert invoke(files, '--apply') != 0
    assert json.loads(capsys.readouterr().out)['status'] == 'error'
    assert target.read_bytes() == original
    assert json.loads(providers.read_text())['providers']['qwen_plus']['model'] == 'qwen3.8-max'


def test_second_replace_failure_rolls_back_first_and_keeps_backups(files, monkeypatch, capsys):
    module = tool()
    original = [path.read_bytes() for path in files]
    replace = module.os.replace
    failed = [False]
    def fail_second(source, target):
        if Path(target) == files[1] and not failed[0]:
            failed[0] = True
            raise OSError('private-error-fixture')
        return replace(source, target)
    monkeypatch.setattr(module.os, 'replace', fail_second)
    assert invoke(files, '--apply') != 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['status'] == 'error'
    assert result['code'] == 'apply_failed_rolled_back'
    assert 'private-' not in output
    assert [path.read_bytes() for path in files] == original
    assert len(result['backups']) == 2
    assert not list(files[0].parent.glob('*.qwen37-stage-*'))


def test_concurrent_change_is_preserved_and_not_partially_migrated(files, monkeypatch, capsys):
    module = tool()
    providers_before = files[1].read_bytes()
    copy_file = module._copy
    changed = [False]
    def concurrent_copy(record, data, *, backup=False):
        result = copy_file(record, data, backup=backup)
        if not backup and not changed[0]:
            changed[0] = True
            files[0].write_text('DASHSCOPE_MODEL=concurrent-other-model\n')
        return result
    monkeypatch.setattr(module, '_copy', concurrent_copy)
    assert invoke(files, '--apply') != 0
    capsys.readouterr()
    assert files[0].read_text() == 'DASHSCOPE_MODEL=concurrent-other-model\n'
    assert files[1].read_bytes() == providers_before
    assert not list(files[0].parent.glob('*.qwen37-stage-*'))


def test_escaped_or_concatenated_model_values_are_not_exact_old_literal(files, capsys):
    files[0].write_text('DASHSCOPE_MODEL=qwen3.8-ma\\x\nDASHSCOPE_VL_MODEL=qwen"3.8-max"\n')
    original = files[0].read_bytes()
    assert invoke(files, '--apply') == 0
    result = json.loads(capsys.readouterr().out)
    assert result['changes']['env'] == 0
    assert files[0].read_bytes() == original


def test_comment_and_quoted_other_values_survive(files, capsys):
    files[0].write_text('export DASHSCOPE_MODEL = "qwen3.8-max"  # retain comment\r\nDASHSCOPE_VL_MODEL=qwen3.8-max\r\nDASHSCOPE_API_KEY="private key # literal"\r\n')
    assert invoke(files, '--apply') == 0
    capsys.readouterr()
    text = files[0].read_bytes()
    assert b'export DASHSCOPE_MODEL = "qwen3.7-plus"  # retain comment\r\n' in text
    assert b'DASHSCOPE_VL_MODEL=qwen3.7-plus\r\n' in text
    assert b'DASHSCOPE_API_KEY="private key # literal"\r\n' in text
