import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deploy/windows-stack/stack.py'


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT.exists():
            return
        spec = importlib.util.spec_from_file_location('stack_launcher', SCRIPT)
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)

    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'Windows stack launcher not implemented')
        self.config = dict(SonicStart=r'C:\Sonic Agent\start.bat', RunnerStart=r'C:\Runner\start.cmd',
                           FrpExe=r'C:\FRP\frpc.exe', FrpConfig=r'C:\FRP\frpc.toml',
                           RemoteHost='101.34.197.12', RemotePort=17789)

    def state(self, component, name='', command='', path=''):
        processes = [dict(Name=name, CommandLine=command, ExecutablePath=path)] if name else []
        return self.app.component_state(component, processes, self.config)

    def test_existing_programs_and_unrelated_python(self):
        self.assertEqual(self.state('sonic', 'java.exe', 'java -jar sonic-agent.jar'), 'running')
        self.assertEqual(self.state('runner', 'python.exe', 'python windows-midscene-runner.py'), 'running')
        self.assertEqual(self.state('runner', 'python.exe', 'python other.py'), 'stopped')
        self.assertEqual(self.state('sonic'), 'stopped')

    def test_starting_wrapper_blocks_duplicate(self):
        self.assertEqual(self.state('sonic', 'cmd.exe', 'cmd /c "C:\\Sonic Agent\\start.bat"'), 'starting')

    def test_frp_identity_and_unknown_process(self):
        self.assertEqual(self.state('frp', 'frpc.exe', 'frpc -c "C:\\FRP\\frpc.toml"', self.config['FrpExe']), 'running')
        self.assertEqual(self.state('frp', 'frpc.exe', 'frpc -c other.toml', self.config['FrpExe']), 'conflict')
        self.assertEqual(self.state('frp', 'frpc.exe', None), 'unknown')
        self.assertEqual(self.state('sonic', 'java.exe', None), 'unknown')

    def test_batch_quoting_and_metacharacters(self):
        self.assertEqual(self.app.batch_command(self.config['SonicStart']),
                         'cmd.exe /d /s /c ""C:\\Sonic Agent\\start.bat""')
        for value in ['C:\\a%PATH%\\s.bat', 'C:\\a!b\\s.bat', 'C:\\a"b\\s.bat', 'C:\\a\ns.bat']:
            with self.assertRaises(ValueError):
                self.app.batch_command(value)

    def test_validate_all_paths_before_start(self):
        with tempfile.TemporaryDirectory() as temp:
            c = self.config.copy()
            for key, filename in [('SonicStart', 's.bat'), ('RunnerStart', 'r.cmd'), ('FrpExe', 'frpc.exe'), ('FrpConfig', 'frpc.toml')]:
                p = pathlib.Path(temp) / filename
                p.touch()
                c[key] = str(p)
            self.app.validate_config(c, False)
            c['RunnerStart'] = ''
            self.app.validate_config(c, True)
            with self.assertRaises(ValueError):
                self.app.validate_config(c, False)
            c['FrpConfig'] += 'missing'
            with self.assertRaises(ValueError):
                self.app.validate_config(c, True)

    def test_does_not_launch_existing_or_unknown_components(self):
        for state in ('running', 'starting', 'conflict', 'unknown'):
            calls = []
            result = self.app.ensure_component(lambda: state, lambda: calls.append(True), timeout=0)
            self.assertEqual(calls, [])
            self.assertEqual(result, state)

    def test_start_success_and_failure_not_conflated(self):
        states = iter(['stopped', 'running', 'running'])
        calls = []
        self.assertEqual(self.app.ensure_component(lambda: next(states), lambda: calls.append(True)), 'running')
        self.assertEqual(calls, [True])
        self.assertEqual(self.app.ensure_component(lambda: 'stopped', lambda: None, timeout=0), 'stopped')

    def test_briefly_alive_then_exited_is_not_ready(self):
        from unittest.mock import patch
        states = iter(['stopped', 'running', 'stopped'])
        with patch.object(self.app.time, 'monotonic', side_effect=[0, 0, 2]), patch.object(self.app.time, 'sleep'):
            result = self.app.ensure_component(lambda: next(states), lambda: None, timeout=1)
        self.assertEqual(result, 'stopped')

    def test_stopped_service_does_not_duplicate_manual_runner(self):
        self.assertEqual(self.app.runner_service_action('Stopped', 'running'), 'manual')
        self.assertEqual(self.app.runner_service_action('Stopped', 'unknown'), 'manual')
        self.assertEqual(self.app.runner_service_action('Running', 'running'), 'service')
        self.assertEqual(self.app.runner_service_action('Stopped', 'stopped'), 'start')
        self.assertEqual(self.app.runner_service_action('StartPending', 'stopped'), 'wait')

    def test_direct_runner_requires_existing_environment(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                self.app.runner_environment()
        with patch.dict('os.environ', {'MIDSCENE_RUNNER_TOKEN': 'private-local-value'}, clear=True):
            self.assertEqual(self.app.runner_environment()['PYTHONUTF8'], '1')

    def test_failed_process_query_is_not_empty_inventory(self):
        def fail(*a, **kw):
            raise subprocess.CalledProcessError(1, 'powershell')
        with self.assertRaises(RuntimeError):
            self.app.get_inventory(run=fail)

    def test_inventory_single_objects_and_empty_arrays(self):
        def one(*a, **kw):
            return subprocess.CompletedProcess([], 0, '{"Processes":{"Name":"java.exe"},"RunnerService":null}')
        self.assertEqual(self.app.get_inventory(run=one)['Processes'][0]['Name'], 'java.exe')
        def empty(*a, **kw):
            return subprocess.CompletedProcess([], 0, '{"Processes":null,"RunnerService":null}')
        self.assertEqual(self.app.get_inventory(run=empty)['Processes'], [])

    def test_frp_missing_config_not_success_and_invalid_port(self):
        for port in (0, 65536, 'wrong', True):
            c = self.config.copy()
            c['RemotePort'] = port
            with self.assertRaises(ValueError):
                self.app.validate_config(c, False)


if __name__ == '__main__':
    unittest.main()
