"""金额与课时：一律以「分」存储，禁止浮点；折算过程可展示、可复核。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def yuan_to_cents(value) -> int:
    """元 → 分。接受 str/Decimal/int/float，浮点先转字符串避免二进制误差。"""
    if isinstance(value, int):
        return value
    d = Decimal(str(value))
    return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def cents_to_yuan(cents: int) -> str:
    return format(Decimal(int(cents)) / Decimal(100), '.2f')


def format_cents(cents: int) -> str:
    return '¥' + format(Decimal(int(cents)) / Decimal(100), ',.2f')


def unit_price_cents(price_cents: int, total_sessions: int) -> int:
    """单价 = 售价 ÷ 总课时，向下取整到分（尾差归入最后一节）。"""
    if total_sessions <= 0:
        raise ValueError('总课时必须大于 0')
    return int(price_cents) // int(total_sessions)


def refund_amount_cents(price_cents: int, total_sessions: int, used_sessions: int) -> int:
    """按已用课时比例折算退款：售价 - 单价 × 已用课时。"""
    if total_sessions <= 0:
        raise ValueError('总课时必须大于 0')
    used = max(0, min(int(used_sessions), int(total_sessions)))
    return int(price_cents) - unit_price_cents(price_cents, total_sessions) * used


def refund_formula_text(price_cents: int, total_sessions: int, used_sessions: int) -> str:
    unit = unit_price_cents(price_cents, total_sessions)
    remain = total_sessions - used_sessions
    refund = refund_amount_cents(price_cents, total_sessions, used_sessions)
    return '%s ÷ %d 节 = %s/节，剩余 %d 节 → 退 %s' % (
        format_cents(price_cents), total_sessions, format_cents(unit), remain, format_cents(refund))
