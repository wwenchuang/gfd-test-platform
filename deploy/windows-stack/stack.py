"""Start existing Windows Sonic/Runner/FRP installations without changing secrets."""
import argparse
import ctypes
import json
import ntpath
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

HOME = Path(__file__).resolve().parent
CONFIG = HOME / 'stack.local.json'
SERVICE = 'MidsceneWindowsRunner'
DEFAULTS = {
    'SonicStart': r'D:\sonic\sonic-agent-v2.7.2-windows_x86_64\sonic-agent-windows-x86_64.jar',
    'RunnerStart': r'D:\sonic\midscene_run\windows-midscene-runner.py',
    'FrpExe': r'C:\frp\frpc.exe',
    'FrpConfig': r'C:\frp\frpc.toml',
    'JavaExe': '',
    'RemoteHost': '101.34.197.12',
    'RemotePort': 17789,
}
LABELS = {'sonic': 'Sonic 手机客户端', 'frp': 'FRP 远控通道', 'runner': 'Windows 任务执行器'}
STATES = {'running': '进程已运行（服务就绪需另行验证）', 'starting': '启动脚本仍在运行，尚未检测到目标进程',
          'stopped': '未检测到运行进程', 'conflict': '已有其他 FRP 配置在运行，未重复启动',
          'unknown': '进程信息不可读，未重复启动，请检查读取权限'}


def batch_command(path):
    if any(char in path for char in '%!"\r\n'):
        raise ValueError('批处理路径包含 CMD 会展开的特殊字符，请移到不含 %、!、双引号或换行的目录')
    return 'cmd.exe /d /s /c ""' + path + '""'


def component_state(component, processes, config):
    """Conservatively skip recognizable existing instances; never kill/adopt them."""
    result = 'stopped'
    for process in processes:
        name = (process.get('Name') or '').lower()
        command = (process.get('CommandLine') or '').lower()
        if component == 'frp':
            if name != 'frpc.exe':
                continue
            if not command or not process.get('ExecutablePath'):
                return 'unknown'
            if (ntpath.normcase(process['ExecutablePath']) != ntpath.normcase(config['FrpExe'])
                    or config['FrpConfig'].lower() not in command):
                return 'conflict'
            result = 'running'
        else:
            relevant = name in ('java.exe', 'javaw.exe') if component == 'sonic' else name in ('python.exe', 'pythonw.exe', 'python3.exe')
            marker = 'sonic-agent' if component == 'sonic' else 'windows-midscene-runner.py'
            if relevant and not command:
                return 'unknown'
            if relevant and marker in command:
                result = 'running'
            key = 'SonicStart' if component == 'sonic' else 'RunnerStart'
            wrapper = config.get(key, '').lower()
            if name == 'cmd.exe' and wrapper and wrapper in command and result == 'stopped':
                result = 'starting'
    return result


def validate_config(config, runner_service):
    port = config.get('RemotePort')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('远控端口必须是 1 到 65535 的整数')
    host = config.get('RemoteHost')
    if not isinstance(host, str) or not host.strip() or any(c.isspace() for c in host) or '://' in host:
        raise ValueError('远控地址填写主机名或 IP，不包含 http:// 或空格')
    fields = [('SonicStart', ('.bat', '.cmd', '.jar')), ('FrpExe', ('.exe',)),
              ('FrpConfig', ('.toml', '.ini', '.yaml', '.yml', '.json'))]
    if not runner_service:
        fields.append(('RunnerStart', ('.bat', '.cmd', '.py')))
    for key, extensions in fields:
        value = config.get(key)
        if not isinstance(value, str) or not value or not Path(value).is_file():
            raise ValueError(f'{key} 文件不存在，请运行 configure.cmd 重新选择')
        if Path(value).suffix.lower() not in extensions:
            raise ValueError(f'{key} 文件类型不正确')
        if Path(value).suffix.lower() in ('.bat', '.cmd'):
            batch_command(value)
    if Path(config['FrpExe']).name.lower() != 'frpc.exe':
        raise ValueError('请选择客户端 frpc.exe，不是服务端 frps.exe')
    if Path(config['SonicStart']).suffix.lower() == '.jar':
        java = config.get('JavaExe') or shutil.which('java.exe')
        if not java or not Path(java).is_file():
            raise ValueError('未找到 Java，请运行 configure.cmd 选择现有 java.exe')


def powershell(command, run=subprocess.run):
    # Read/operate only existing Windows services. No policy changes or secret output.
    exe = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
              'System32/WindowsPowerShell/v1.0/powershell.exe')
    prefix = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; "
    return run([exe, '-NoProfile', '-NonInteractive', '-Command', prefix + command],
               check=True, capture_output=True, text=True, encoding='utf-8', timeout=25,
               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout


def get_inventory(run=subprocess.run):
    try:
        raw = powershell("$p=@(Get-CimInstance Win32_Process | Select-Object Name,CommandLine,ExecutablePath); "
                         "$s=Get-Service -Name 'MidsceneWindowsRunner' -ErrorAction SilentlyContinue; "
                         "@{Processes=$p; RunnerService=$(if($s){$s.Status.ToString()}else{$null})} | ConvertTo-Json -Depth 4 -Compress", run)
        data = json.loads(raw.lstrip('\ufeff'))
        processes = data['Processes']
        if processes is None:
            processes = []
        if isinstance(processes, dict):
            processes = [processes]
        if not isinstance(processes, list):
            raise ValueError('invalid inventory')
        data['Processes'] = processes
        return data
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise RuntimeError('无法读取 Windows 进程/服务状态；为避免重复启动，本次已停止。请检查 PowerShell/CIM 是否可用。') from exc


def ensure_component(observe, launch, timeout=12):
    state = observe()
    if state != 'stopped':
        return state
    launch()
    deadline = time.monotonic() + timeout
    running_seen = False
    while True:
        state = observe()
        if state in ('conflict', 'unknown'):
            return state
        if state == 'running' and running_seen:
            return state
        running_seen = state == 'running'
        if time.monotonic() >= deadline:
            return 'starting' if state == 'running' else state
        time.sleep(1)


def configure(existing, runner_service):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError as exc:
        raise RuntimeError('当前 Python 缺少文件选择组件 tkinter，请使用安装了 Tcl/Tk 的原 Python 环境，或手工填写 stack.local.json 中的路径。') from exc
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    candidate = dict(existing)
    try:
        messagebox.showinfo('配置一键启动', '请确认现有程序路径。只保存文件位置，不读取或修改密码、令牌。取消任何选择都不会启动程序。', parent=root)
        choices = [('SonicStart', '选择 Sonic Agent 的 JAR 或原启动脚本', [('Sonic 程序', '*.jar *.bat *.cmd')])]
        if not runner_service:
            choices.append(('RunnerStart', '选择 windows-midscene-runner.py 或原启动批处理', [('Runner 程序', '*.py *.bat *.cmd')]))
        choices.extend([('FrpExe', '选择 FRP 客户端 frpc.exe', [('FRP 客户端', 'frpc.exe')]),
                        ('FrpConfig', '选择现有 FRP 客户端配置（通常是 frpc.toml）', [('配置文件', '*.toml *.ini *.yaml *.yml *.json')])])
        for key, title, kinds in choices:
            old = Path(candidate.get(key) or '.')
            selected = filedialog.askopenfilename(parent=root, title=title, initialdir=str(old.parent) if old.parent.exists() else None,
                                                  initialfile=old.name, filetypes=kinds)
            if not selected:
                print('已取消，未保存配置，未启动程序。')
                return None
            candidate[key] = str(Path(selected).resolve())
        if Path(candidate['SonicStart']).suffix.lower() == '.jar':
            java = candidate.get('JavaExe') or shutil.which('java.exe')
            if not java or not Path(java).is_file():
                java = filedialog.askopenfilename(parent=root, title='选择 Sonic 使用的现有 java.exe', filetypes=[('Java', 'java.exe')])
                if not java:
                    print('已取消，未保存配置，未启动程序。')
                    return None
            candidate['JavaExe'] = str(Path(java).resolve())
        validate_config(candidate, runner_service)
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(CONFIG)
        print('配置已保存：' + str(CONFIG))
        return candidate
    finally:
        root.destroy()


def runner_service_action(service_state, process_state):
    if service_state == 'Running':
        return 'service'
    if process_state != 'stopped':
        return 'manual'
    return 'start' if service_state == 'Stopped' else 'wait'


def runner_environment():
    environment = dict(os.environ)
    if environment.get('MIDSCENE_RUNNER_TOKEN', '').strip() in ('', 'midscene2026', 'change-me', 'changeme', 'test', 'token'):
        raise ValueError('直接启动 Runner 缺少有效 MIDSCENE_RUNNER_TOKEN。请使用已有 Runner 服务、原来设置环境的批处理，或在本机配置现有令牌；不要把令牌发给别人。')
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    return environment


def launch_component(component, config):
    if component == 'frp':
        logs = HOME / 'logs'
        logs.mkdir(exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        out = logs / ('frp-' + stamp + '.out.log')
        err = logs / ('frp-' + stamp + '.err.log')
        with out.open('wb') as stdout, err.open('wb') as stderr:
            subprocess.Popen([config['FrpExe'], '-c', config['FrpConfig']], cwd=str(Path(config['FrpExe']).parent),
                             stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        print('  FRP 日志：' + str(out) + ' / ' + str(err))
    else:
        path = config['SonicStart' if component == 'sonic' else 'RunnerStart']
        environment = None
        if Path(path).suffix.lower() == '.py':
            environment = runner_environment()
            command = [sys.executable, '-u', path]
        elif Path(path).suffix.lower() == '.jar':
            command = [config.get('JavaExe') or shutil.which('java.exe'), '-jar', path]
        else:
            command = batch_command(path)
        if Path(path).suffix.lower() in ('.jar', '.py'):
            logs = HOME / 'logs'
            logs.mkdir(exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
            out = logs / (component + '-' + stamp + '.out.log')
            err = logs / (component + '-' + stamp + '.err.log')
            with out.open('wb') as stdout, err.open('wb') as stderr:
                subprocess.Popen(command, cwd=str(Path(path).parent), env=environment,
                                 stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            print('  日志：' + str(out) + ' / ' + str(err))
        else:
            subprocess.Popen(command, cwd=str(Path(path).parent), env=environment, creationflags=subprocess.CREATE_NEW_CONSOLE)


def tcp_check(config):
    try:
        with socket.create_connection((config['RemoteHost'], config['RemotePort']), timeout=4):
            print('远控入口：TCP 可连接。还需在 Safari 中确认手机画面，不能仅凭端口判断远控成功。')
            return True
    except OSError:
        print('远控入口：暂不可连接。检查 FRP 日志中的认证、连接和代理注册信息；脚本不会自动修改令牌。')
        return False


def acquire_lock():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, 'Global\\MidsceneWindowsStackLauncher')
    error = ctypes.get_last_error()
    if not handle:
        raise RuntimeError('无法创建启动锁，未启动程序。请检查当前账户权限。')
    if error == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError('已有启动或检查窗口正在执行，请等待该窗口结束后再操作。')
    return kernel, handle


def main():
    parser = argparse.ArgumentParser(description='Sonic、Windows Runner、FRP 一键启动')
    parser.add_argument('action', choices=('start', 'status', 'configure'), nargs='?', default='start')
    args = parser.parse_args()
    if os.name != 'nt':
        print('此工具只能在连接手机的 Windows 电脑运行。')
        return 1
    kernel, handle = acquire_lock()
    try:
        config = dict(DEFAULTS)
        if CONFIG.exists():
            try:
                config.update(json.loads(CONFIG.read_text(encoding='utf-8-sig')))
            except (ValueError, OSError):
                raise ValueError('本地配置无法读取，请保留原文件并检查 JSON 格式')
        inventory = get_inventory()
        service = inventory.get('RunnerService')
        if args.action == 'start' and not CONFIG.exists():
            config['JavaExe'] = shutil.which('java.exe') or ''
            try:
                validate_config(config, bool(service))
            except ValueError:
                pass  # Missing/moved files are selected explicitly below.
            else:
                CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
                print('已找到截图中确认的程序位置，保存本机路径配置。')
        if args.action == 'configure' or (args.action == 'start' and not CONFIG.exists()):
            config = configure(config, bool(service))
            if config is None:
                return 1
            if args.action == 'configure':
                return 0
        validate_config(config, bool(service))  # Validate every input before starting anything.
        all_ok = True
        for component in ('sonic', 'frp', 'runner'):
            print('\n' + LABELS[component])
            try:
                if component == 'runner' and service:
                    current_inventory = get_inventory()
                    state = component_state('runner', current_inventory['Processes'], config)
                    decision = runner_service_action(current_inventory.get('RunnerService'), state)
                    if decision == 'manual':
                        print('  已有手动实例或信息不明确，不启动第二个服务实例：' + STATES[state])
                        all_ok = all_ok and state == 'running'
                        continue
                    if args.action == 'start' and decision == 'start':
                        powershell("Start-Service -Name 'MidsceneWindowsRunner'; (Get-Service -Name 'MidsceneWindowsRunner').WaitForStatus('Running',[TimeSpan]::FromSeconds(15))")
                    current = get_inventory().get('RunnerService')
                    service_label = {'Running': '正在运行', 'Stopped': '已停止', 'StartPending': '正在启动', 'StopPending': '正在停止', 'Paused': '已暂停'}.get(current, '状态待确认')
                    print('  复用现有服务：' + service_label + '（任务心跳请在平台确认）')
                    all_ok = all_ok and current == 'Running'
                    continue
                observe = lambda: component_state(component, get_inventory()['Processes'], config)
                state = ensure_component(observe, lambda: launch_component(component, config)) if args.action == 'start' else observe()
                print('  ' + STATES[state])
                all_ok = all_ok and state == 'running'
            except ValueError as error:
                print('  ' + str(error))
                all_ok = False
            except (OSError, RuntimeError, subprocess.SubprocessError):
                print('  启动/检查失败。查看该组件窗口或日志；已有程序保持运行。')
                all_ok = False
        print('')
        reachable = tcp_check(config)
        print('\n检查完成。进程和端口不等于最终业务验收，请继续确认平台 Runner 心跳及 Sonic 手机画面。')
        return 0 if all_ok and reachable else 1
    finally:
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print('未完成：' + str(error))
        sys.exit(1)
