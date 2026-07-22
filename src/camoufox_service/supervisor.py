"""Worker 子进程监管、队列准入、硬超时、替换与回收。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from collections.abc import Sequence

import psutil

logger = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    pass


class WorkerExited(WorkerError):
    pass


class WorkerTimeout(WorkerError):
    pass


class QueueFull(WorkerError):
    pass


class WorkerProcess:
    """管理单个 Worker 子进程及其 JSONL 请求、响应和资源统计。"""

    def __init__(
        self,
        worker_id: int,
        command: Sequence[str],
        cwd: str | None = None,
        stream_limit_bytes: int = 16 * 1024 * 1024,
    ):
        self.worker_id = worker_id
        self.command = list(command)
        self.cwd = cwd
        self.stream_limit_bytes = stream_limit_bytes
        self.process: asyncio.subprocess.Process | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.started_at = 0.0
        self.jobs = 0
        self.sessions = 0

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process and self.process.returncode is None else None

    async def start(self) -> None:
        """启动独立进程组，并创建 stdout/stderr 后台读取任务。"""

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
            limit=self.stream_limit_bytes,
        )
        self.started_at = time.monotonic()
        self.reader_task = asyncio.create_task(self._read_stdout())
        self.stderr_task = asyncio.create_task(self._drain_stderr())

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                    future = self.pending.pop(str(message.get("id")), None)
                    if future is None or future.done():
                        continue
                    if message.get("error"):
                        future.set_exception(
                            WorkerError(str(message["error"].get("message") or "worker error"))
                        )
                    else:
                        future.set_result(message.get("result") or {})
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        "worker %s emitted invalid protocol output: %s", self.worker_id, exc
                    )
        except ValueError:
            self._fail_pending(
                WorkerError(
                    f"worker {self.worker_id} response exceeded {self.stream_limit_bytes} bytes"
                )
            )
            if self.process and self.process.returncode is None:
                self.process.kill()
        finally:
            self._fail_pending(WorkerExited(f"worker {self.worker_id} exited"))

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while await self.process.stderr.readline():
            pass

    def _fail_pending(self, error: Exception) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()

    async def request(self, kind: str, payload: dict) -> dict:
        """发送带唯一 ID 的 JSONL 请求，并等待对应异步响应。"""

        if not self.process or self.process.returncode is not None or not self.process.stdin:
            raise WorkerExited(f"worker {self.worker_id} is not running")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        message = json.dumps(
            {"id": request_id, "kind": kind, "payload": payload}, separators=(",", ":")
        )
        try:
            self.process.stdin.write((message + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self.pending.pop(request_id, None)
            raise WorkerExited(f"worker {self.worker_id} pipe closed") from exc
        result = await future
        if kind != "health":
            self.jobs += 1
        if kind == "session.create" or (kind == "challenge.solve" and result.get("sessionId")):
            self.sessions += 1
        elif kind in {"session.destroy", "challenge.session.destroy"}:
            self.sessions = max(0, self.sessions - 1)
        return result

    def rss_mb(self) -> float:
        if not self.pid:
            return 0.0
        try:
            root = psutil.Process(self.pid)
            processes = [root, *root.children(recursive=True)]
            return (
                sum(process.memory_info().rss for process in processes if process.is_running())
                / 1024
                / 1024
            )
        except (psutil.Error, OSError):
            return 0.0

    async def stop(self) -> None:
        """终止 Worker 及浏览器后代进程，并收束所有后台任务。"""

        self._fail_pending(WorkerExited(f"worker {self.worker_id} stopped"))
        pid = self.pid
        if pid:
            # 先处理完整进程树，避免只结束 Worker 后遗留 Camoufox 子进程。
            try:
                root = psutil.Process(pid)
                processes = [*root.children(recursive=True), root]
                for process in processes:
                    try:
                        process.terminate()
                    except psutil.Error:
                        pass
                _, alive = psutil.wait_procs(processes, timeout=2)
                for process in alive:
                    try:
                        process.kill()
                    except psutil.Error:
                        pass
            except (psutil.Error, OSError):
                if self.process and self.process.returncode is None:
                    self.process.kill()
        if self.process:
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=1)
                except (TimeoutError, ProcessLookupError):
                    pass
        current = asyncio.current_task()
        tasks = []
        for task in (self.reader_task, self.stderr_task):
            if task and task is not current and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.process = None


class WorkerSupervisor:
    """管理 Worker 池、队列容量、硬超时、代际和主动回收。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        workers: int = 1,
        queue_size: int = 8,
        task_timeout: float = 120,
        cwd: str | None = None,
        max_jobs: int = 50,
        max_lifetime: int = 1800,
        max_rss_mb: int = 1536,
        stream_limit_bytes: int = 16 * 1024 * 1024,
    ):
        self.command = list(command)
        self.worker_count = workers
        self.queue_size = queue_size
        self.task_timeout = task_timeout
        self.cwd = cwd
        self.max_jobs = max_jobs
        self.max_lifetime = max_lifetime
        self.max_rss_mb = max_rss_mb
        self.stream_limit_bytes = stream_limit_bytes
        self._workers: dict[int, WorkerProcess] = {}
        self._generations = {worker_id: 0 for worker_id in range(workers)}
        self._locks = {worker_id: asyncio.Lock() for worker_id in range(workers)}
        self._admission_lock = asyncio.Lock()
        self._admitted = 0
        self._next_worker = 0
        self.restarts = 0
        self.total_requests = 0
        self.failed_requests = 0

    @property
    def pids(self) -> list[int]:
        return [pid for worker_id in sorted(self._workers) if (pid := self._workers[worker_id].pid)]

    async def start(self) -> None:
        if self._workers:
            return
        try:
            for worker_id in range(self.worker_count):
                await self._start_worker(worker_id)
        except Exception:
            await self.stop()
            raise

    async def _start_worker(self, worker_id: int) -> WorkerProcess:
        """启动并健康检查 Worker，成功后递增该槽位代际。"""

        worker = WorkerProcess(
            worker_id,
            self.command,
            self.cwd,
            stream_limit_bytes=self.stream_limit_bytes,
        )
        await worker.start()
        try:
            await asyncio.wait_for(worker.request("health", {}), timeout=max(10, self.task_timeout))
        except Exception:
            await worker.stop()
            raise
        self._workers[worker_id] = worker
        self._generations[worker_id] += 1
        return worker

    async def stop(self) -> None:
        workers = list(self._workers.values())
        self._workers.clear()
        await asyncio.gather(*(worker.stop() for worker in workers), return_exceptions=True)

    def ready(self) -> bool:
        return len(self.pids) == self.worker_count

    def generation(self, worker_id: int) -> int:
        return self._generations[worker_id]

    async def _admit(self) -> None:
        async with self._admission_lock:
            if self._admitted >= self.worker_count + self.queue_size:
                raise QueueFull("worker queue is full")
            self._admitted += 1

    async def _release_admission(self) -> None:
        async with self._admission_lock:
            self._admitted -= 1

    def _select_worker_id(self) -> int:
        for worker_id, lock in self._locks.items():
            if not lock.locked():
                return worker_id
        worker_id = self._next_worker % self.worker_count
        self._next_worker += 1
        return worker_id

    async def _replace(self, worker_id: int) -> WorkerProcess:
        """完整停止旧进程，再为同一槽位创建新 Worker。"""

        old = self._workers.pop(worker_id, None)
        if old:
            await old.stop()
        self.restarts += 1
        return await self._start_worker(worker_id)

    def _should_recycle(self, worker: WorkerProcess) -> bool:
        """无持久 Session 时，根据任务数、存活时间和 RSS 判断回收。"""

        if worker.sessions:
            return False
        age = time.monotonic() - worker.started_at
        return (
            worker.jobs >= self.max_jobs
            or age >= self.max_lifetime
            or worker.rss_mb() >= self.max_rss_mb
        )

    async def request(
        self,
        kind: str,
        payload: dict,
        timeout: float | None = None,
        worker_id: int | None = None,
    ) -> dict:
        result, _ = await self.request_with_worker(
            kind, payload, timeout=timeout, worker_id=worker_id
        )
        return result

    async def request_with_worker(
        self,
        kind: str,
        payload: dict,
        timeout: float | None = None,
        worker_id: int | None = None,
    ) -> tuple[dict, int]:
        """按 admission、Worker 锁、硬超时和进程替换顺序执行任务。"""

        # admission 同时统计运行中和排队任务，先限流再选择 Worker。
        await self._admit()
        selected = self._select_worker_id() if worker_id is None else worker_id
        if selected not in self._locks:
            await self._release_admission()
            raise WorkerError(f"worker {selected} does not exist")
        lock = self._locks[selected]
        try:
            async with lock:
                worker = self._workers.get(selected)
                if worker is None or worker.pid is None:
                    worker = await self._replace(selected)
                try:
                    result = await asyncio.wait_for(
                        worker.request(kind, payload),
                        timeout=timeout or self.task_timeout,
                    )
                    self.total_requests += 1
                except TimeoutError as exc:
                    self.failed_requests += 1
                    await self._replace(selected)
                    raise WorkerTimeout(
                        f"worker task exceeded {timeout or self.task_timeout} seconds"
                    ) from exc
                except WorkerError:
                    self.failed_requests += 1
                    await self._replace(selected)
                    raise
                if self._should_recycle(worker):
                    await self._replace(selected)
                return result, selected
        finally:
            await self._release_admission()

    def metrics(self) -> dict:
        return {
            "workers": self.worker_count,
            "readyWorkers": len(self.pids),
            "queuedAndRunning": self._admitted,
            "totalRequests": self.total_requests,
            "failedRequests": self.failed_requests,
            "workerRestarts": self.restarts,
            "workerPids": self.pids,
            "workerRssMb": {
                str(worker_id): round(worker.rss_mb(), 2)
                for worker_id, worker in self._workers.items()
            },
        }
