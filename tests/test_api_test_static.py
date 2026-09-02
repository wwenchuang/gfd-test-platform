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
    def test_committed_bundle_contains_current_retry_scope_labels(self):
        root = Path(_PROJECT_ROOT) / 'api-test'
        bundles = list((root / 'assets').glob('*.js'))

        self.assertTrue(bundles, 'API testing build must include a JavaScript bundle')
        bundle_text = '\n'.join(path.read_text(encoding='utf-8') for path in bundles)
        self.assertIn('仅重跑当前失败项', bundle_text)
        self.assertIn('重跑全部失败项', bundle_text)

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

    def test_main_platform_assets_revalidate_after_each_deploy(self):
        for path in ('/js/agent-workbench.js', '/css/round5.css'):
            handler = _StaticHandler()

            self.assertTrue(_serve_static(handler, path))

            self.assertEqual(handler.status, 200)
            self.assertIn(('Cache-Control', 'no-cache, must-revalidate'), handler.headers)

    def test_nginx_deploy_path_revalidates_main_platform_assets(self):
        root = Path(_PROJECT_ROOT)
        nginx = (root / 'deploy' / 'nginx-midscene-task.conf').read_text(encoding='utf-8')
        cache_override = (root / 'deploy' / 'nginx-static-cache.conf').read_text(encoding='utf-8')
        installer = (root / 'deploy' / 'install-server.sh').read_text(encoding='utf-8')

        self.assertIn('Cache-Control "no-cache, must-revalidate" always', nginx)
        self.assertIn('map $uri $midscene_static_cache_control', cache_override)
        self.assertIn('add_header Cache-Control $midscene_static_cache_control always', cache_override)
        self.assertIn('nginx-static-cache.conf', installer)


if __name__ == '__main__':
    unittest.main()
