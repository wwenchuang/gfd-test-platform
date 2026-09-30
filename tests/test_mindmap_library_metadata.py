import json
from task_server.services import yaml_service as service


def test_app_ownership_uses_explicit_summary_or_saved_request_not_module(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'CASE_DIR', str(tmp_path / 'cases'))
    monkeypatch.setattr(service, 'asset_meta_path', lambda key: str(tmp_path / (key + '.json')))
    for case_id, summary, meta, expected in [
        ('new', {'app_package':'com.new', 'module':'共同模块'}, {'app_package':'com.old'}, 'com.new'),
        ('old', {'module':'共同模块'}, {'appPackage':'com.old'}, 'com.old'),
        ('unknown', {'module':'智小白3D'}, {}, ''),
    ]:
        directory=tmp_path / 'cases' / case_id
        directory.mkdir(parents=True)
        (directory / 'summary.json').write_text(json.dumps(summary))
        (tmp_path / (case_id+'.json')).write_text(json.dumps(meta))
        record=service.generation_mindmap_record(case_id)
        assert record['app_package'] == expected


def test_new_summary_persists_saved_application(tmp_path, monkeypatch):
    meta=tmp_path/'meta.json'
    meta.write_text(json.dumps({'app_package':'com.test.app'}))
    monkeypatch.setattr(service, 'asset_meta_path', lambda _:str(meta))
    summary=service.build_generation_summary('test','title','module','',{})
    assert summary['app_package']=='com.test.app'
