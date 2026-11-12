"""类型化错误体系：客户端只看到规范化结构，绝不返回堆栈或内部细节。"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AppError(Exception):
    code = 'INTERNAL_ERROR'
    http_status = 500
    message = '服务内部错误'

    def __init__(self, message: Optional[str] = None, *, details: Any = None, code: Optional[str] = None):
        self.message = message or self.message
        self.details = details
        if code:
            self.code = code
        super().__init__(self.message)

    def to_dict(self, request_id: str = '') -> Dict[str, Any]:
        err: Dict[str, Any] = {'code': self.code, 'message': self.message}
        if request_id:
            err['request_id'] = request_id
        if self.details is not None:
            err['details'] = self.details
        return {'error': err}


class ValidationError(AppError):
    code, http_status, message = 'VALIDATION_ERROR', 400, '请求参数不合法'


class Unauthorized(AppError):
    code, http_status, message = 'UNAUTHORIZED', 401, '登录状态无效或已过期'


class Forbidden(AppError):
    code, http_status, message = 'FORBIDDEN', 403, '无权访问该资源'


class TenantViolation(Forbidden):
    code, message = 'TENANT_VIOLATION', '越权访问：目标数据不属于当前租户'


class NotFound(AppError):
    code, http_status, message = 'NOT_FOUND', 404, '请求的资源不存在'


class Conflict(AppError):
    code, http_status, message = 'CONFLICT', 409, '资源状态冲突'


class IdempotencyConflict(Conflict):
    code, message = 'IDEMPOTENCY_CONFLICT', '相同幂等键已用于不同的业务参数'


class BusinessRuleError(AppError):
    code, http_status, message = 'BUSINESS_RULE_VIOLATION', 422, '业务规则校验未通过'


class InsufficientEntitlement(BusinessRuleError):
    code, message = 'INSUFFICIENT_ENTITLEMENT', '课时或余额不足'


class ClassFull(BusinessRuleError):
    code, message = 'CLASS_FULL', '课程名额已满，可加入候补'


class EntitlementNotApplicable(BusinessRuleError):
    code, message = 'ENTITLEMENT_NOT_APPLICABLE', '当前权益不适用于该门店或课程'


class BookingClosed(BusinessRuleError):
    code, message = 'BOOKING_CLOSED', '已超过可约时间窗'


class DuplicateBooking(Conflict):
    code, message = 'DUPLICATE_BOOKING', '同一时段已有预约'


class RateLimited(AppError):
    code, http_status, message = 'RATE_LIMITED', 429, '请求过于频繁，请稍后再试'


ERROR_REGISTRY = {
    cls.code: cls.http_status
    for cls in (
        AppError, ValidationError, Unauthorized, Forbidden, TenantViolation,
        NotFound, Conflict, IdempotencyConflict, BusinessRuleError,
        InsufficientEntitlement, ClassFull, EntitlementNotApplicable,
        BookingClosed, DuplicateBooking, RateLimited,
    )
}
