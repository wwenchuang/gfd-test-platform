"""The Mac Runner obtains its vision model configuration before launching a case."""
import importlib.util
from pathlib import Path
from urllib.error import HTTPError, URLError


def load_runner():
    path = Path(__file__).resolve().parents[1] / "mac-midscene-runner.py"
    spec = importlib.util.spec_from_file_location("mac_runner_runtime_test", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def test_transient_config_failure_retries_before_launch(monkeypatch):
    runner = load_runner()
    replies = iter([URLError("temporary timeout"), {"env": {"MIDSCENE_MODEL_API_KEY": "fixture"}}])

    def fetch(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(runner, "http_json", fetch)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    assert runner.task_runtime_env() == {"MIDSCENE_MODEL_API_KEY": "fixture"}
    assert runner.task_runtime_env() == {"MIDSCENE_MODEL_API_KEY": "fixture"}


def test_auth_rejection_clears_expired_cache_and_next_request_recovers(monkeypatch):
    runner = load_runner()
    runner._RUNTIME_ENV_CACHE = {"MIDSCENE_MODEL_API_KEY": "old-fixture"}

    def forbidden(*args, **kwargs):
        raise HTTPError("https://example.invalid", 403, "forbidden", {}, None)

    monkeypatch.setattr(runner, "http_json", forbidden)
    assert runner.task_runtime_env(force=True) == {}
    monkeypatch.setattr(runner, "http_json", lambda *args, **kwargs: {"env": {"MIDSCENE_MODEL_API_KEY": "new-fixture"}})
    assert runner.task_runtime_env() == {"MIDSCENE_MODEL_API_KEY": "new-fixture"}


def test_config_without_a_key_does_not_hide_recovery(monkeypatch):
    runner = load_runner()
    replies = iter([
        {"env": {"MIDSCENE_MODEL_NAME": "fixture-model"}},
        {"env": {"MIDSCENE_MODEL_API_KEY": "fixture"}},
    ])
    monkeypatch.setattr(runner, "http_json", lambda *args, **kwargs: next(replies))
    # Keep non-secret settings available to a Runner using local credentials.
    assert runner.task_runtime_env() == {"MIDSCENE_MODEL_NAME": "fixture-model"}
    assert runner.task_runtime_env() == {"MIDSCENE_MODEL_API_KEY": "fixture"}
