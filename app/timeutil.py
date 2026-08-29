"""时间工具：业务里 date_time 表示「北京时间当天 0 点」的 UTC 时间戳（即减 8 小时）。"""
from datetime import date, datetime, timedelta, timezone

TZ8 = timezone(timedelta(hours=8))


def now_ts() -> int:
    return int(datetime.now(TZ8).timestamp())


def today_sh() -> date:
    return datetime.now(TZ8).date()


def shanghai_midnight_ts(d: date) -> int:
    """北京时间 d 日 0 点对应的时间戳。"""
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ8).timestamp())


def pg_dow(ts: int) -> int:
    """PG 的 dow：周日=0..周六=6（按北京时间）。"""
    return (datetime.fromtimestamp(ts, TZ8).weekday() + 1) % 7


def py_weekday(ts: int) -> int:
    """Python weekday：周一=0..周日=6（按北京时间）。"""
    return datetime.fromtimestamp(ts, TZ8).weekday()
