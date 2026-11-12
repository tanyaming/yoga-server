"""进程内登录失败限流：轻量、零外部依赖，单进程部署有效。

多进程 / 多实例生产部署应改用外部存储（Redis）或反向代理层 limit_req，
本模块作为单实例兜底保护，避免账号被无成本暴力破解（BUG-010）。
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}

# 阈值：同一账号 5 分钟内失败 5 次后，锁定 60 秒
MAX_FAILURES = 5
WINDOW_SECONDS = 300
LOCK_SECONDS = 60


def _prune(key: str, now: float) -> list[float]:
    ts = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    if ts:
        _failures[key] = ts
    else:
        _failures.pop(key, None)
    return ts


def is_blocked(key: str, now: float | None = None) -> bool:
    """是否处于锁定状态（最近一次失败距今未超锁定时长，且窗口内失败次数达标）。"""
    if not key:
        return False
    now = now if now is not None else time.time()
    with _lock:
        ts = _prune(key, now)
        if len(ts) < MAX_FAILURES:
            return False
        return now - ts[-1] < LOCK_SECONDS


def record_failure(key: str) -> None:
    if not key:
        return
    now = time.time()
    with _lock:
        ts = _prune(key, now)
        ts.append(now)
        _failures[key] = ts


def reset(key: str) -> None:
    if not key:
        return
    with _lock:
        _failures.pop(key, None)
