import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


class ApiSmokeTest(unittest.TestCase):
    def test_auth_rejection_positive_and_intentional_failure(self):
        path = Path(__file__).with_name('api_smoke.py')
        self.assertTrue(path.exists(), 'API smoke implementation missing')
        spec = importlib.util.spec_from_file_location('week1_api_smoke', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'fetch_result', return_value=(200, {'code': 4009, 'data': None})):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(module.main(['--scenario', 'anonymous']), 0)
                self.assertEqual(module.main(['--scenario', 'anonymous', '--expected-code', '999']), 1)
        self.assertNotIn('sensitive-user-data', output.getvalue())

    def test_authenticated_success_requires_a_list_and_never_outputs_body(self):
        path = Path(__file__).with_name('api_smoke.py')
        self.assertTrue(path.exists(), 'API smoke implementation missing')
        spec = importlib.util.spec_from_file_location('week1_api_smoke', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict('os.environ', {'W01_API_AUTHORIZATION': 'secret-value'}):
            with patch.object(module, 'fetch_result', return_value=(200, {'code': 0, 'data': ['sensitive-user-data']})):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(module.main(['--scenario', 'authenticated']), 0)
        self.assertNotIn('secret-value', output.getvalue())
        self.assertNotIn('sensitive-user-data', output.getvalue())
        with patch.dict('os.environ', {'W01_API_AUTHORIZATION': 'secret-value'}):
            with patch.object(module, 'fetch_result', return_value=(200, {'code': 0, 'data': {}})):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(['--scenario', 'authenticated']), 1)


if __name__ == '__main__':
    unittest.main()
