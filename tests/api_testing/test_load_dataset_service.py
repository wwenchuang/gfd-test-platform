"""Private load-dataset storage tests."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from task_server.api_testing.models.load_testing import ApiLoadDataset
from task_server.api_testing.services.load_dataset_service import (
    MAX_DATASET_BYTES,
    LoadDatasetError,
    LoadDatasetService,
)
from tests.api_testing.test_load_testing_repository import load_factory, load_records


class _Session:
    def __init__(self):
        self.project = SimpleNamespace(id="project-1", owner_id="tester")
        self.records = []
        self.info = {}

    def get(self, model, identifier):
        return self.project if identifier == self.project.id else None

    def scalar(self, statement):
        return self.project.id

    def add(self, record):
        record.id = "dataset-1"
        self.records.append(record)

    def flush(self):
        return None


class _Factory:
    def __init__(self):
        self.session = _Session()

    @contextmanager
    def begin(self):
        yield self.session


class _FailingCommitFactory(_Factory):
    @contextmanager
    def begin(self):
        yield self.session
        raise RuntimeError("database commit failed")


@pytest.fixture()
def dataset_service(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "task_server.api_testing.access.get_access_profile", lambda actor: None
    )
    factory = _Factory()
    service = LoadDatasetService(factory, data_root=tmp_path / "private-load-data")
    return service, factory


def test_import_utf8_csv_stores_canonical_private_file_and_redacted_preview(dataset_service):
    service, factory = dataset_service

    result = service.import_bytes(
        "project-1",
        "城市账号",
        "users.csv",
        "城市,手机号\n北京,13812345678\n上海,13987654321\n".encode(),
        "fixed_per_vu",
        "tester",
    )

    assert result.id == "dataset-1"
    assert result.row_count == 2
    assert result.fields == ("城市", "手机号")
    assert result.preview_rows[0] == {"城市": "北京", "手机号": "138****5678"}
    stored = Path(factory.session.records[0].storage_ref)
    assert stored.parent == service.data_root
    assert stored.name != "users.csv"
    assert json.loads(stored.read_text()) == [
        {"城市": "北京", "手机号": "13812345678"},
        {"城市": "上海", "手机号": "13987654321"},
    ]
    assert os.stat(stored).st_mode & 0o777 == 0o600
    assert os.stat(service.data_root).st_mode & 0o777 == 0o700
    assert factory.session.records[0].content_hash == result.content_hash


@pytest.mark.parametrize("mode", ["cycle", "fixed_per_vu", "exclusive_per_iteration"])
def test_all_three_dataset_modes_are_persisted(dataset_service, mode):
    service, factory = dataset_service

    result = service.import_bytes(
        "project-1", "关键字", "keywords.json", '[{"keyword":"收纳盒"}]'.encode(), mode, "tester"
    )

    assert result.usage_mode == mode
    assert factory.session.records[-1].usage_mode == mode


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("duplicate.csv", b"name,name\na,b\n", "字段名不能重复"),
        ("short.csv", b"name,city\na\n", "第 2 行列数"),
        ("object.json", b'{"name":"a"}', "JSON 顶层必须是数组"),
        ("uneven.json", b'[{"name":"a"},{"name":"b","city":"x"}]', "字段必须一致"),
        ("duplicate.json", b'[{"name":"a","name":"b"}]', "字段名不能重复"),
    ],
)
def test_invalid_csv_or_json_shape_is_rejected(dataset_service, filename, content, message):
    service, _ = dataset_service

    with pytest.raises(LoadDatasetError, match=message):
        service.import_bytes("project-1", "坏数据", filename, content, "cycle", "tester")


def test_path_traversal_and_oversized_upload_are_rejected_before_write(dataset_service):
    service, factory = dataset_service

    with pytest.raises(LoadDatasetError, match="文件名不能包含路径"):
        service.import_bytes("project-1", "越界", "../users.csv", b"id\n1\n", "cycle", "tester")
    with pytest.raises(LoadDatasetError, match="文件不能超过"):
        service.import_bytes(
            "project-1", "过大", "huge.csv", b"x" * (MAX_DATASET_BYTES + 1), "cycle", "tester"
        )

    assert not factory.session.records
    assert not service.data_root.exists()


def test_secret_columns_must_use_environment_assets(dataset_service):
    service, _ = dataset_service

    with pytest.raises(LoadDatasetError, match="不能包含密码、Token 或密钥"):
        service.import_bytes(
            "project-1", "错误账号", "users.csv", b"username,password\na,secret\n", "cycle", "tester"
        )


def test_unknown_mode_and_missing_project_are_rejected(dataset_service):
    service, factory = dataset_service
    with pytest.raises(LoadDatasetError, match="数据取用方式"):
        service.import_bytes("project-1", "数据", "rows.csv", b"id\n1\n", "random", "tester")

    factory.session.project.id = "another-project"
    with pytest.raises(LoadDatasetError, match="项目不存在"):
        service.import_bytes("project-1", "数据", "rows.csv", b"id\n1\n", "cycle", "tester")


def test_database_commit_failure_removes_the_private_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "task_server.api_testing.access.get_access_profile", lambda actor: None
    )
    service = LoadDatasetService(
        _FailingCommitFactory(), data_root=tmp_path / "private-load-data"
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        service.import_bytes(
            "project-1", "关键字", "rows.csv", b"keyword\nbox\n", "cycle", "tester"
        )

    assert list(service.data_root.iterdir()) == []


def test_import_persists_the_private_reference_in_postgres(
    tmp_path, monkeypatch, load_factory, load_records
):
    monkeypatch.setattr(
        "task_server.api_testing.access.get_access_profile", lambda actor: None
    )
    service = LoadDatasetService(load_factory, data_root=tmp_path / "load-data")

    result = service.import_bytes(
        load_records["project"].id,
        "真实持久化",
        "rows.json",
        '[{"keyword":"收纳盒"}]'.encode(),
        "cycle",
        "load-owner",
    )

    with load_factory() as session:
        stored = session.scalar(
            select(ApiLoadDataset).where(ApiLoadDataset.id == result.id)
        )
    assert stored is not None
    assert stored.project_id == load_records["project"].id
    assert Path(stored.storage_ref).is_file()
    assert stored.content_hash == result.content_hash
