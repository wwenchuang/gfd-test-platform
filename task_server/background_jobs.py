"""Bounded dispatcher for persisted AI generation and repair jobs."""

import queue
import threading
import traceback

from .config import BACKGROUND_JOB_QUEUE_SIZE, BACKGROUND_JOB_WORKERS


SUPPORTED_BACKGROUND_JOB_TYPES = {"generate", "mindmap_only", "figma_parse", "repair"}


class HeavyWorkloadLimiter:
    """Share one concurrency budget across synchronous and queued heavy work."""

    def __init__(self, capacity):
        self.capacity = max(1, int(capacity))
        self._slots = threading.BoundedSemaphore(self.capacity)
        self._lock = threading.Lock()
        self._active = 0
        self._sync_active = 0
        self._rejected_total = 0

    def acquire(self, kind, blocking=False):
        acquired = self._slots.acquire(blocking=blocking)
        with self._lock:
            if not acquired:
                self._rejected_total += 1
                return False
            self._active += 1
            if kind == "sync":
                self._sync_active += 1
        return True

    def release(self, kind):
        with self._lock:
            self._active = max(0, self._active - 1)
            if kind == "sync":
                self._sync_active = max(0, self._sync_active - 1)
        self._slots.release()

    def metrics(self):
        with self._lock:
            return {
                "heavy_workloads_active": self._active,
                "heavy_workloads_max": self.capacity,
                "sync_heavy_workloads_active": self._sync_active,
                "heavy_workloads_rejected_total": self._rejected_total,
            }


def _default_job_loader(job_id):
    from .services.yaml_service import load_generate_job

    return load_generate_job(job_id)


def _default_runner_resolver(job_type):
    if job_type == "repair":
        from .services.repair_service import run_repair_job

        return run_repair_job
    from .services.yaml_service import (
        run_figma_parse_job,
        run_generate_job,
        run_mindmap_only_job,
    )

    return {
        "generate": run_generate_job,
        "mindmap_only": run_mindmap_only_job,
        "figma_parse": run_figma_parse_job,
    }.get(job_type)


def _default_failure_recorder(job_id, message, error_trace=""):
    from .services.yaml_service import update_generate_job

    update_generate_job(
        job_id,
        ok=False,
        status="failed",
        step="后台调度失败",
        message=message,
        error=message,
        error_trace=error_trace,
    )


class PersistedJobDispatcher:
    """Run a fixed number of persisted jobs while queueing only their IDs."""

    def __init__(
        self,
        worker_count,
        queue_size,
        job_loader=None,
        runner_resolver=None,
        failure_recorder=None,
    ):
        self.worker_count = max(1, int(worker_count))
        self.queue_size = max(1, int(queue_size))
        self._queue = queue.Queue(maxsize=self.queue_size)
        self._job_loader = job_loader or _default_job_loader
        self._runner_resolver = runner_resolver or _default_runner_resolver
        self._failure_recorder = failure_recorder or _default_failure_recorder
        self._lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._threads = []
        self._scheduled = set()
        self._running = set()
        self._waiting_for_workload_slot = set()
        self._rejected_total = 0

    def start(self):
        with self._lock:
            if self._stopped:
                raise RuntimeError("后台任务调度器已经停止")
            if self._started:
                return False
            self._started = True
            for index in range(self.worker_count):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"persisted-job-worker-{index + 1}",
                    daemon=True,
                )
                self._threads.append(worker)
                worker.start()
        return True

    def submit(self, job_id):
        job_id = str(job_id or "").strip()
        if not job_id:
            return False
        self.start()
        with self._lock:
            if job_id in self._scheduled:
                return False
            self._scheduled.add(job_id)
            try:
                self._queue.put_nowait(job_id)
            except queue.Full:
                self._scheduled.discard(job_id)
                self._rejected_total += 1
                return False
        return True

    def _worker_loop(self):
        while True:
            job_id = self._queue.get()
            workload_slot_acquired = False
            try:
                if job_id is None:
                    return
                job = self._job_loader(job_id)
                if not isinstance(job, dict) or job.get("status") != "pending":
                    continue
                job_type = str(job.get("type") or "").strip().lower()
                runner = self._runner_resolver(job_type)
                request_data = job.get("request_data") or job.get("requestData")
                if runner is None:
                    self._record_failure(job_id, f"不支持的后台任务类型：{job_type or '未提供'}")
                    continue
                if not isinstance(request_data, dict):
                    self._record_failure(job_id, "后台任务缺少可执行的原始请求，请重新发起")
                    continue
                with self._lock:
                    self._waiting_for_workload_slot.add(job_id)
                workload_slot_acquired = acquire_heavy_workload_slot("background", blocking=True)
                with self._lock:
                    self._waiting_for_workload_slot.discard(job_id)
                    self._running.add(job_id)
                runner(job_id, request_data)
            except Exception as exc:
                self._record_failure(
                    job_id,
                    f"后台任务调度失败：{exc}",
                    traceback.format_exc()[-4000:],
                )
            finally:
                with self._lock:
                    self._running.discard(job_id)
                    self._waiting_for_workload_slot.discard(job_id)
                    self._scheduled.discard(job_id)
                if workload_slot_acquired:
                    release_heavy_workload_slot("background")
                self._queue.task_done()

    def _record_failure(self, job_id, message, error_trace=""):
        try:
            self._failure_recorder(job_id, message, error_trace)
        except Exception as exc:
            print(f"后台任务失败状态写入失败：job={job_id} error={exc}", flush=True)

    def metrics(self):
        with self._lock:
            return {
                "background_workers": self.worker_count,
                "background_running": len(self._running),
                "background_waiting_for_slot": len(self._waiting_for_workload_slot),
                "background_queued": self._queue.qsize(),
                "background_queue_capacity": self.queue_size,
                "background_rejected_total": self._rejected_total,
            }

    def stop(self):
        with self._lock:
            if not self._started or self._stopped:
                return
            self._stopped = True
            threads = list(self._threads)
        for _thread in threads:
            self._queue.put(None)
        for thread in threads:
            thread.join(timeout=2)


_HEAVY_WORKLOAD_LIMITER = HeavyWorkloadLimiter(BACKGROUND_JOB_WORKERS)


def acquire_heavy_workload_slot(kind="sync", blocking=False):
    return _HEAVY_WORKLOAD_LIMITER.acquire(kind, blocking=blocking)


def release_heavy_workload_slot(kind="sync"):
    _HEAVY_WORKLOAD_LIMITER.release(kind)


_DISPATCHER = PersistedJobDispatcher(
    worker_count=BACKGROUND_JOB_WORKERS,
    queue_size=BACKGROUND_JOB_QUEUE_SIZE,
)


def start_persisted_background_dispatcher():
    return _DISPATCHER.start()


def enqueue_persisted_background_job(job_id):
    return _DISPATCHER.submit(job_id)


def background_job_runtime_metrics():
    return {**_DISPATCHER.metrics(), **_HEAVY_WORKLOAD_LIMITER.metrics()}


def restore_persisted_background_jobs(limit=None):
    """Restore queued jobs and make interrupted running jobs explicit."""
    from .services.yaml_service import iter_raw_generate_jobs, load_generate_job, update_generate_job

    start_persisted_background_dispatcher()
    restored = 0
    interrupted = 0
    rejected = 0
    for raw_job in iter_raw_generate_jobs(limit=limit):
        job_id = ""
        try:
            job_type = str(raw_job.get("type") or "").strip().lower()
            if job_type not in SUPPORTED_BACKGROUND_JOB_TYPES:
                continue
            job_id = str(raw_job.get("job_id") or "").strip()
            if not job_id:
                continue
            job = load_generate_job(job_id) or raw_job
            status = str(job.get("status") or "").strip().lower()
            if status == "running":
                message = "服务重启中断了正在执行的后台任务，请重新发起或使用重试功能"
                update_generate_job(
                    job_id,
                    ok=False,
                    status="failed",
                    step="服务重启中断",
                    message=message,
                    error=message,
                )
                interrupted += 1
                continue
            if status != "pending":
                continue
            request_data = job.get("request_data") or job.get("requestData")
            if not isinstance(request_data, dict):
                _default_failure_recorder(job_id, "服务重启后无法恢复：任务缺少原始请求，请重新发起")
                interrupted += 1
                continue
            if enqueue_persisted_background_job(job_id):
                restored += 1
                continue
            _default_failure_recorder(job_id, "服务重启后恢复队列已满，请稍后使用重试功能")
            rejected += 1
        except Exception as exc:
            print(f"后台任务恢复失败：job={job_id or '未知'} error={exc}", flush=True)
    return {"restored": restored, "interrupted": interrupted, "rejected": rejected}
