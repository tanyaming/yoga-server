"""微信 code2session 代理：原仓库唯一值得照搬的可复用件，补上超时与错误处理。"""
from __future__ import annotations

import hashlib
from typing import Dict

import requests

from ..config import settings
from ..errors import Unauthorized, ValidationError
from ..logging_config import get_logger

logger = get_logger('app.wechat')
_URL = 'https://api.weixin.qq.com/sns/jscode2session'
_TIMEOUT = 5


def code2session(code: str) -> Dict[str, str]:
    if not code or not code.strip():
        raise ValidationError('微信 code 不能为空')
    if settings.WX_MOCK or not settings.WX_APPID or not settings.WX_SECRET:
        # 本地联调：以 code 派生稳定 openid，保证同一开发者工具反复登录拿到同一会员
        fake = 'mock_' + hashlib.sha256(code.strip().encode()).hexdigest()[:24]
        return {'openid': fake, 'session_key': '', 'mock': 'true'}
    try:
        resp = requests.get(_URL, params={
            'appid': settings.WX_APPID,
            'secret': settings.WX_SECRET,
            'js_code': code.strip(),
            'grant_type': 'authorization_code',
        }, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning('微信登录接口调用失败', extra={'extra': {'reason': type(exc).__name__}})
        raise Unauthorized('微信登录服务暂不可用，请稍后重试') from exc
    if data.get('errcode') or not data.get('openid'):
        raise Unauthorized('微信登录失败：' + str(data.get('errmsg') or '无效 code'))
    return {'openid': data['openid'], 'session_key': data.get('session_key', ''), 'mock': 'false'}
