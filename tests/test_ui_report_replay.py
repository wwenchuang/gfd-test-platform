import json
import pytest
from task_server.services.ui_report_replay import build_replay_document


def source(extra=''):
    return ('<script>alert("original-code")</script><script type="midscene_web_dump" data-group-id="g" onclick="bad()">'+json.dumps({'executions':[], 'groupName':'</script><script>injected()</script>'}).replace('<', r'\u003c')+'</script><script type="midscene-image" data-id="pic">data:image/png;base64,aGVsbG8=</script>'+extra).encode()


def test_rebuild_keeps_only_data_and_trusted_renderer():
    doc=build_replay_document(source())
    assert 'original-code' not in doc and 'onclick="bad()"' not in doc
    assert '<script>injected()' not in doc
    assert r'\u003c/script\u003e' in doc
    assert 'data:image/png;base64,aGVsbG8=' in doc
    assert 'midscene_web_dump' in doc and 'default-src' in doc


def test_external_and_svg_image_data_rejected():
    for value in ['https://example.org/secret', 'data:image/svg+xml;base64,YQ==']:
        with pytest.raises(ValueError,match='replay_image_invalid'):
            build_replay_document(source('<script type="midscene-image" data-id="bad">'+value+'</script>'))


def test_non_report_and_malformed_json_explicitly_fail():
    for body in [b'<h1>old report</h1>',b'<script type="midscene_web_dump">not-json</script>']:
        with pytest.raises(ValueError,match='replay_'):
            build_replay_document(body)
