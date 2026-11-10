import gzip
import io
import unittest
import zipfile

from task_server.services.week5_trace_scan import safe_trace


def archive(items):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as result:
        for name, contents in items:
            result.writestr(name, contents)
    return raw.getvalue()


class TraceScanTest(unittest.TestCase):
    def test_checks_uncompressed_and_nested_gzip_for_raw_and_encoded_token(self):
        token = 'EXAMPLE_TOKEN_0123456789abcdefghijklmno'
        import base64
        variants = [token.encode(), base64.b64encode(token.encode()), token.encode().hex().encode()]
        for value in variants:
            self.assertFalse(safe_trace(archive([('trace.network', b'prefix' + value)]), token))
            self.assertFalse(safe_trace(archive([('resources/x.gz', gzip.compress(value))]), token))
        self.assertTrue(safe_trace(archive([('trace.trace', b'{"event":"ready"}')]), token))

    def test_rejects_corrupt_and_nested_zip(self):
        token = 'EXAMPLE_TOKEN_0123456789abcdefghijklmno'
        self.assertFalse(safe_trace(b'not a zip', token))
        self.assertFalse(safe_trace(archive([('inner.zip', archive([('x', token.encode())]))]), token))
