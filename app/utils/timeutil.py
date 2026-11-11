"""时间工具：统一北京时间（UTC+8），照搬原仓库口径，避免服务端与小程序时区漂移。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BJ_TZ = timezone(timedelta(hours=8))


def now_bj() -> datetime:
    return datetime.now(BJ_TZ)


def now_naive() -> datetime:
    """DB 统一存 naive 的北京时间，避免 SQLite/MySQL 时区差异。"""
    return now_bj().replace(tzinfo=None)


def to_ts(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ_TZ)
    return int(dt.timestamp())


def business_date(dt: datetime | None = None) -> str:
    return (dt or now_naive()).strftime('%Y-%m-%d')


def parse_dt(value: str) -> datetime:
    """接受 'YYYY-MM-DD HH:MM' / ISO8601，返回 naive 北京时间。"""
    value = (value or '').strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError('时间格式不合法，应为 YYYY-MM-DD HH:MM')


def hours_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0
