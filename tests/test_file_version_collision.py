from task_server.services import case_service, yaml_service


def test_same_second_saves_keep_both_restorable_versions(monkeypatch, tmp_path):
    task_dir = tmp_path / "tasks"
    version_dir = tmp_path / "versions"
    (task_dir / "A").mkdir(parents=True)
    source = task_dir / "A" / "case.yaml"
    monkeypatch.setattr(yaml_service, "TASK_DIR", str(task_dir))
    monkeypatch.setattr(yaml_service, "VERSION_DIR", str(version_dir))
    monkeypatch.setattr(yaml_service.time, "strftime", lambda *_args: "20260924_120000")

    source.write_text("first", encoding="utf-8")
    first = yaml_service.save_file_version("A", "case.yaml", reason="save")
    source.write_text("second", encoding="utf-8")
    second = yaml_service.save_file_version("A", "case.yaml", reason="save")

    assert first["id"] != second["id"]
    assert len(case_service.list_file_versions("A", "case.yaml")) == 2
    assert case_service.read_file_version("A", "case.yaml", first["id"])[1] == "first"
    assert case_service.read_file_version("A", "case.yaml", second["id"])[1] == "second"
