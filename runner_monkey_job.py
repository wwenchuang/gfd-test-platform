"""Run one claimed stability test and report observed driver events to the workbench."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time


def available(platform, driver, adb, ios_enabled):
    if not Path(driver).is_file():
        return False
    if platform == 'android':
        return bool(shutil.which(adb))
    return platform == 'ios' and ios_enabled and all(shutil.which(tool) for tool in (
        'iproxy', 'idevice_id', 'ideviceinfo', 'ideviceinstaller', 'idevicecrashreport'))


def execute(job, runner_id, platform, driver, progress, lock_fd=None):
    payload = {'runner_id': runner_id, 'device_id': job.get('device_id', ''),
               'monkey_claim_token': job.get('monkey_claim_token', ''), 'executed': 0,
               'log': '', 'message': '正在准备真机', 'elapsed_seconds': 0}
    process = None
    parent_write = None
    interrupted = None
    outcome = 'inconclusive'
    try:
        if (job.get('runner_id') != runner_id or job.get('platform') != platform
                or not payload['device_id'] or not job.get('target_package') or not payload['monkey_claim_token']):
            raise ValueError('执行目标与领取任务的 Runner 不一致')
        params = {key: job[key] for key in ('platform', 'device_id', 'seed', 'mode')}
        duration = job.get('duration_seconds')
        count = job.get('event_count')
        if duration is not None:
            if type(duration) is not int or not 60 <= duration <= 86400 or count is not None:
                raise ValueError('持续时长或结束条件异常')
            params['duration_seconds'] = duration
            deadline_seconds = duration + 180
        else:
            if type(count) is not int or not 1 <= count <= 10000:
                raise ValueError('事件数异常')
            params['event_count'] = count
            deadline_seconds = 90 + 12 * count
        params['app_id'] = job['target_package']
        if progress(dict(payload)).get('stop_requested'):
            return dict(payload, status='stopped', message='已在启动前停止', process_stopped=True)
        parent_read, parent_write = os.pipe()
        try:
            process = subprocess.Popen([sys.executable, '-u', str(driver)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                       start_new_session=True, env=dict(os.environ, MIDSCENE_MONKEY_PARENT_PIPE_FD=str(parent_read)),
                                       pass_fds=(parent_read,) if lock_fd is None else (parent_read, lock_fd))
        finally:
            os.close(parent_read)
        process.stdin.write(json.dumps(params))
        process.stdin.close()
        messages = queue.Queue()

        def read_events():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)

        threading.Thread(target=read_events, daemon=True).start()
        start = time.monotonic()
        last_contact = start
        next_poll = start
        final = None
        while True:
            now = time.monotonic()
            if now >= next_poll:
                try:
                    reply = progress(dict(payload))
                    last_contact = time.monotonic()
                    if reply.get('stop_requested'):
                        interrupted = 'stopped'
                        break
                except (OSError, ValueError, RuntimeError):
                    if time.monotonic() - last_contact >= 15:
                        interrupted = 'connection'
                        break
                next_poll = time.monotonic() + 1
            if now - start >= deadline_seconds:
                interrupted = 'timeout'
                break
            try:
                line = messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            frame = json.loads(line)
            if frame.get('type') not in ('progress', 'result'):
                raise ValueError('执行器返回了无法识别的事件')
            n = frame.get('executed')
            if type(n) is not int or n < payload['executed'] or (count is not None and n > count):
                raise ValueError('执行器操作计数异常')
            elapsed = frame.get('elapsed_seconds', payload['elapsed_seconds'])
            if type(elapsed) is not int or elapsed < payload['elapsed_seconds']:
                raise ValueError('执行器计时异常')
            payload.update(elapsed_seconds=elapsed, executed=n, message=str(frame.get('message') or '')[:500])
            if frame.get('log'):
                payload['log'] = str(frame['log'])[:16000]
            if frame['type'] == 'result':
                final = frame
        if interrupted:
            outcome = 'stopped' if interrupted == 'stopped' else 'inconclusive'
            payload['message'] = {'stopped':'已按请求停止', 'connection':'与工作台失去联系，已停止操作',
                                  'timeout':'达到执行时限，已停止操作'}[interrupted]
        elif final and final.get('status') in ('passed', 'crashed', 'inconclusive', 'failed'):
            outcome = final['status']
            if outcome in ('crashed', 'failed') and final.get('crash_log'):
                payload['crash_log'] = str(final['crash_log'])[:16000]
            if outcome == 'crashed' and not payload.get('crash_log'):
                outcome, payload['message'] = 'inconclusive', '缺少可核对的目标应用崩溃日志'
            if outcome == 'passed':
                reached = payload['elapsed_seconds'] >= duration and payload['executed'] > 0 if duration is not None else payload['executed'] == count
                if not reached or process.wait(timeout=8) != 0:
                    outcome, payload['message'] = 'inconclusive', '执行器退出状态或运行目标未通过核对'
        else:
            payload['message'] = '执行器退出前未返回测试结论'
    except ValueError as error:
        outcome = 'failed'
        payload['message'] = '执行输入或回报格式错误：' + type(error).__name__
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        payload['message'] = '真机执行或回传中断：' + type(error).__name__
    finally:
        if parent_write is not None:
            os.close(parent_write)
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    process.wait()
            process.stdout.close()
    return dict(payload, status=outcome, process_stopped=True)


def _journal(workspace, job_id):
    name = hashlib.sha256(job_id.encode()).hexdigest()
    return Path(workspace) / 'monkey-outbox' / (name + '.json')


def _write(path, state):
    tmp = path.with_suffix('.tmp')
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def durable_execute(job, runner_id, platform, driver, progress, workspace):
    path = _journal(workspace, job['job_id'])
    path.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
    state = {'job_id': job['job_id'], 'payload': {}}
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        def tracked(body):
            state['payload'] = body
            _write(path, state)
            return progress(body)
        result = execute(job, runner_id, platform, driver, tracked, lock.fileno())
        state['payload'] = result
        _write(path, state)
        return result


def acknowledged(workspace, job_id):
    path = _journal(workspace, job_id)
    path.unlink(missing_ok=True)
    path.with_suffix('.lock').unlink(missing_ok=True)


def recover(workspace, post_result):
    folder = Path(workspace) / 'monkey-outbox'
    for path in folder.glob('*.json'):
        with path.with_suffix('.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            record = json.loads(path.read_text())
            payload = record['payload']
            if not payload.get('process_stopped'):
                payload.update(status='inconclusive', process_stopped=True,
                               message='Runner 重启，原驱动已退出。保留最后确认的操作数。')
                _write(path, record)
            post_result(record['job_id'], payload)
            acknowledged(workspace, record['job_id'])
