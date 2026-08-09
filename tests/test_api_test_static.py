import io
import unittest
from pathlib import Path

from task_server.app import _PROJECT_ROOT, _serve_static


class _StaticHandler:
    def __init__(self):
        self.html = None
        self.text = None
        self.status = None
        self.headers = []
        self.wfile = io.BytesIO()

    def _html(self, value, status=200):
        self.html = value
        self.status = status

    def _text(self, value, status=200):
        self.text = value
        self.status = status

    def send_response(self, status):
        self.status = status

    def _cors(self):
        pass

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        pass


class ApiTestStaticFilesTest(unittest.TestCase):
    def test_serves_base_assets_and_spa_refresh(self):
        root = Path(_PROJECT_ROOT) / 'api-test'
        asset = next((root / 'assets').glob('*.js'))

        base = _StaticHandler()
        self.assertTrue(_serve_static(base, '/api-test/'))
        self.assertEqual(base.html, (root / 'index.html').read_text(encoding='utf-8'))

        bundle = _StaticHandler()
        self.assertTrue(_serve_static(bundle, f'/api-test/assets/{asset.name}'))
        self.assertEqual(bundle.status, 200)
        self.assertEqual(bundle.wfile.getvalue(), asset.read_bytes())

        refresh = _StaticHandler()
        self.assertTrue(_serve_static(refresh, '/api-test/runs'))
        self.assertEqual(refresh.html, (root / 'index.html').read_text(encoding='utf-8'))

    def test_rejects_paths_outside_the_api_test_build_directory(self):
        handler = _StaticHandler()

        self.assertTrue(_serve_static(handler, '/api-test/%2e%2e/task-manager.html'))

        self.assertEqual(handler.status, 404)
        self.assertEqual(handler.text, 'api test asset not found')


if __name__ == '__main__':
    unittest.main()
