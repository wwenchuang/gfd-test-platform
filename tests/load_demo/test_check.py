import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "load_demo_check",
    Path(__file__).resolve().parents[2] / "deploy/load-demo/check.py",
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok":true}'


def test_request_retries_transient_startup_reset(monkeypatch):
    attempts = []

    def urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            raise ConnectionResetError("container is still starting")
        return Response()

    monkeypatch.setattr(check.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(check.time, "sleep", lambda _seconds: None)

    response = check.open_when_ready(
        check.urllib.request.Request("http://127.0.0.1:18080/health"),
        attempts=3,
        delay_seconds=0.01,
    )

    assert response.status == 200
    assert len(attempts) == 3


def test_request_does_not_retry_http_application_errors(monkeypatch):
    def urlopen(_request, timeout):
        raise check.urllib.error.HTTPError(
            "http://127.0.0.1:18080/health", 500, "error", {}, None
        )

    monkeypatch.setattr(check.urllib.request, "urlopen", urlopen)

    try:
        check.open_when_ready(
            check.urllib.request.Request("http://127.0.0.1:18080/health"),
            attempts=3,
            delay_seconds=0,
        )
    except check.urllib.error.HTTPError as error:
        assert error.code == 500
    else:
        raise AssertionError("HTTP errors must not be hidden by readiness retries")
