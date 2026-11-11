"""结构化 JSON 日志 + 贯穿请求的 request_id（contextvars）。"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any, Dict

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('request_id', default='')
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('tenant_id', default='')

_SENSITIVE = ('password', 'token', 'secret', 'authorization', 'openid', 'phone', 'id_card')


def _scrub(obj: Any) -> Any:
    """日志脱敏：任何键名含敏感词的字段一律打码。"""
    if isinstance(obj, dict):
        return {k: ('***' if any(s in str(k).lower() for s in _SENSITIVE) else _scrub(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + '...'
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
            'request_id': request_id_var.get(''),
            'tenant_id': tenant_id_var.get(''),
        }
        if hasattr(record, 'extra') and isinstance(record.extra, dict):
            payload.update(_scrub(record.extra))
        if record.exc_info:
            payload['exc'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = 'INFO') -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for noisy in ('uvicorn.access', 'sqlalchemy.engine.Engine'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, msg: str, **extra: Any) -> None:
    logger.log(level, msg, extra={'extra': extra})
