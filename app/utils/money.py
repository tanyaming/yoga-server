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


def prorate_refund_cents(price_cents: int, total_sessions: int, refund_sessions: int) -> int:
    """按「退几节课时」比例折算退款金额：round(售价 × 退课时 / 总课时)。

    用 Decimal + ROUND_HALF_UP，避免二进制浮点与 Python 银行家舍入带来的分位误差。
    """
    if total_sessions <= 0:
        raise ValueError('总课时必须大于 0')
    sessions = int(refund_sessions)
    if sessions < 0 or sessions > int(total_sessions):
        raise ValueError('退款课时必须落在 0..总课时 之间')
    amount = (Decimal(int(price_cents)) * Decimal(sessions) / Decimal(int(total_sessions)))
    return int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def prorate_refund_formula_text(price_cents: int, total_sessions: int,
                                refund_sessions: int) -> str:
    """课时型退款的可复核公式文本，写进 refund_records.formula。"""
    refund = prorate_refund_cents(price_cents, total_sessions, refund_sessions)
    return '%s × %d 节 ÷ %d 节 = %s' % (
        format_cents(price_cents), int(refund_sessions), int(total_sessions),
        format_cents(refund))
