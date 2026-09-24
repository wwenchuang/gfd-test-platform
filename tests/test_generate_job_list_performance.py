import json

from task_server.services import yaml_service


def test_generate_job_list_scans_retry_history_once(tmp_path, monkeypatch):
    generation_dir = tmp_path / "generation"
    generation_dir.mkdir()
    for index in range(300):
        job = {
            "job_id": f"gen-{index:03d}",
            "type": "generate",
            "status": "success",
            "case_set_id": f"case-{index:03d}",
            "request_data": {"case_set_id": f"case-{index:03d}"},
            "result": {"case_set_id": f"case-{index:03d}", "value": index},
        }
        (generation_dir / f"gen-{index:03d}.json").write_text(json.dumps(job))

    monkeypatch.setattr(yaml_service, "GENERATE_JOB_DIR", str(generation_dir))
    monkeypatch.setattr(yaml_service, "generation_summary_path", lambda case_id: str(tmp_path / case_id / "summary.json"))
    monkeypatch.setattr(yaml_service, "asset_meta_path", lambda case_id: str(tmp_path / case_id / "meta.json"))
    monkeypatch.setattr(yaml_service, "load_case_ui_design_meta", lambda case_id: {})
    original_read = yaml_service.read_json_file
    reads = 0

    def count_read(*args, **kwargs):
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(yaml_service, "read_json_file", count_read)
    rows = yaml_service.list_generate_jobs(limit=20)

    assert len(rows) == 20
    assert all(row["can_retry"] for row in rows)
    assert all("request_data" not in row for row in rows)
    assert all(row["result"]["value"] >= 280 for row in rows)
    assert reads <= 440, f"listing 20 rows read {reads} JSON files"


def test_generation_url_index_preserves_latest_match_and_fallback(tmp_path, monkeypatch):
    generation_dir = tmp_path / "generation"
    generation_dir.mkdir()
    for name, url in (("gen-001", "older"), ("gen-002", "newer")):
        (generation_dir / f"{name}.json").write_text(json.dumps({
            "case_set_id": "shared",
            "request_data": {"figma_url": url},
        }))
    monkeypatch.setattr(yaml_service, "GENERATE_JOB_DIR", str(generation_dir))
    monkeypatch.setattr(yaml_service, "load_case_ui_design_meta", lambda case_id: {})
    urls = yaml_service._generation_figma_url_index()

    assert yaml_service.find_figma_url_for_case_set("shared", generation_urls=urls) == "newer"
    assert yaml_service.find_figma_url_for_case_set("shared") == "newer"
    assert yaml_service.find_figma_url_for_case_set("shared", meta={"figma_url": "direct"}, generation_urls=urls) == "direct"
    assert yaml_service.find_figma_url_for_case_set("missing", generation_urls=urls) == ""
