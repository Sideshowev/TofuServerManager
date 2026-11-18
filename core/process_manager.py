"""Process helpers: liveness, whole-tree stop, and CPU/RAM metrics.

This is the one place Tofu touches OS processes directly. ServerManager uses
these helpers for every server it runs, so GUI, agent and Discord bot all see
the same numbers.

psutil is preferred (accurate tree metrics, safe PID-reuse checks). If it is
missing, everything degrades to Windows' tasklist/taskkill instead of crashing.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    import psutil  # type: ignore
    PSUTIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    psutil = None
    PSUTIL_AVAILABLE = False

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass
class ProcessMetrics:
    pid: int
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    memory_percent: float = 0.0
    num_threads: int = 0
    num_processes: int = 1
    uptime_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "memory_percent": round(self.memory_percent, 2),
            "num_threads": self.num_threads,
            "num_processes": self.num_processes,
            "uptime_seconds": round(self.uptime_seconds),
            "timestamp": self.timestamp,
        }


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #
def process_create_time(pid: int) -> Optional[float]:
    if not pid or not PSUTIL_AVAILABLE:
        return None
    try:
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def pid_alive(pid: Optional[int], create_time: Optional[float] = None) -> bool:
    """True if ``pid`` is running. When ``create_time`` is given, also guards
    against Windows recycling the PID for an unrelated process."""
    if not pid:
        return False
    if PSUTIL_AVAILABLE:
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                return False
            if create_time is not None and abs(p.create_time() - create_time) > 2.0:
                return False
            return True
        except Exception:
            return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
            )
            return str(pid) in (out.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _tree(pid: int) -> list:
    try:
        root = psutil.Process(pid)
    except Exception:
        return []
    try:
        kids = root.children(recursive=True)
    except Exception:
        kids = []
    return [root] + kids


def kill_tree(pid: Optional[int], timeout: float = 10.0) -> bool:
    """Stop a process and everything it spawned (cmd.exe -> game -> helpers).

    Returns True if nothing from the tree is left running.
    """
    if not pid:
        return True
    if PSUTIL_AVAILABLE:
        procs = _tree(pid)
        if not procs:
            return True
        for p in reversed(procs):  # children first
            try:
                p.terminate()
            except Exception:
                pass
        _gone, alive = psutil.wait_procs(procs, timeout=timeout)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        if alive:
            _gone, alive = psutil.wait_procs(alive, timeout=5)
        return not alive
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, creationflags=_NO_WINDOW)
        return not pid_alive(pid)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    return not pid_alive(pid)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class MetricsSampler:
    """Samples CPU/RAM for whole process trees.

    psutil's cpu_percent() needs two samples to be meaningful, so Process
    objects are cached between calls.
    """

    def __init__(self):
        self._cache: Dict[int, Any] = {}
        self._lock = threading.Lock()
        try:
            self._ncpu = (psutil.cpu_count() if PSUTIL_AVAILABLE else os.cpu_count()) or 1
        except Exception:
            self._ncpu = 1

    def _proc(self, pid):
        p = self._cache.get(pid)
        if p is None:
            p = psutil.Process(pid)
            try:
                p.cpu_percent(None)  # prime
            except Exception:
                pass
            self._cache[pid] = p
        return p

    def sample(self, pid: Optional[int], started_at: Optional[float] = None) -> Optional[ProcessMetrics]:
        if not pid:
            return None
        if not PSUTIL_AVAILABLE:
            return self._sample_tasklist(pid, started_at)
        with self._lock:
            try:
                root = psutil.Process(pid)
                members = [root] + root.children(recursive=True)
            except Exception:
                return None
            cpu = mem = 0.0
            threads = 0
            seen = set()
            for m in members:
                try:
                    p = self._proc(m.pid)
                    with p.oneshot():
                        cpu += p.cpu_percent(None)
                        mem += p.memory_info().rss
                        threads += p.num_threads()
                    seen.add(m.pid)
                except Exception:
                    continue
            for stale in [k for k in self._cache if k not in seen and not psutil.pid_exists(k)]:
                self._cache.pop(stale, None)
            try:
                total = psutil.virtual_memory().total
            except Exception:
                total = 0
            try:
                start = root.create_time()
            except Exception:
                start = started_at or time.time()
            return ProcessMetrics(
                pid=pid,
                cpu_percent=cpu / self._ncpu,   # percent of the whole machine
                memory_mb=mem / (1024 * 1024),
                memory_percent=(mem / total * 100.0) if total else 0.0,
                num_threads=threads,
                num_processes=len(seen) or 1,
                uptime_seconds=max(0.0, time.time() - (started_at or start)),
            )

    @staticmethod
    def _sample_tasklist(pid, started_at):
        if os.name != "nt":
            return None
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
            )
            parts = [p.replace('"', "") for p in out.stdout.strip().split('","')]
            mem_kb = int("".join(ch for ch in parts[-1] if ch.isdigit()) or 0)
        except Exception:
            return None
        return ProcessMetrics(pid=pid, memory_mb=mem_kb / 1024,
                              uptime_seconds=time.time() - (started_at or time.time()))


def predict_resource_exhaustion(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rough memory-leak detector over a metrics history (oldest first)."""
    if len(history) < 2:
        return {"prediction": "insufficient_data"}
    first, last = history[0], history[-1]
    span = (last.get("timestamp", 0) - first.get("timestamp", 0)) or 0
    mem_delta = (last.get("memory_mb") or 0) - (first.get("memory_mb") or 0)
    per_hour = (mem_delta / span * 3600) if span > 0 else 0.0
    try:
        total = psutil.virtual_memory().total / (1024 * 1024) if PSUTIL_AVAILABLE else None
    except Exception:
        total = None
    hours_left = None
    if per_hour > 0 and total:
        hours_left = max(0.0, (total - (last.get("memory_mb") or 0)) / per_hour)
    return {
        "prediction": "stable" if abs(mem_delta) < 50 else ("growing" if mem_delta > 0 else "shrinking"),
        "memory_trend_mb_per_hour": round(per_hour, 1),
        "hours_to_exhaustion": round(hours_left, 1) if hours_left is not None else None,
        "data_points": len(history),
    }


class ProcessManager:
    """Backwards-compatible facade kept for code written against the old API.

    New code should use ServerManager, which owns server processes.
    """

    def __init__(self):
        self._sampler = MetricsSampler()
        self._history: Dict[str, List[ProcessMetrics]] = {}
        self._callbacks: List[Callable[[str, ProcessMetrics], None]] = []

    def collect_for_pid(self, server_id: str, pid: int, started_at: Optional[float] = None):
        m = self._sampler.sample(pid, started_at)
        if m is not None:
            hist = self._history.setdefault(server_id, [])
            hist.append(m)
            del hist[:-1000]
            for cb in self._callbacks:
                try:
                    cb(server_id, m)
                except Exception:
                    pass
        return m

    def get_metrics_history(self, server_id: str, limit: int = 100) -> List[ProcessMetrics]:
        return self._history.get(server_id, [])[-limit:]

    def get_latest_metrics(self, server_id: str) -> Optional[ProcessMetrics]:
        h = self._history.get(server_id)
        return h[-1] if h else None

    def register_metrics_callback(self, callback: Callable[[str, ProcessMetrics], None]) -> None:
        self._callbacks.append(callback)
