"""统一通知入口（FR-21）：站内信必达 + 微信订阅消息可插拔通道。

设计约束：
- 站内信写入与业务在同一事务，保证「约课成功必有站内信」；
- 订阅消息属于外部副作用，失败不回滚业务，但必须把结果（含跳过原因）落库留痕；
- 模板资质未就绪（PRD 依赖项标注「待申请」）时默认关闭，**不假装送达**，
  而是以 channel_status=SKIPPED_* 明确标注，便于上线前核对。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Dict

from sqlalchemy.orm import Session

from ..config import settings
from ..models import InAppMessage, Member
from ..security import random_id

SCENES = (
    'BOOKING', 'WAITLIST_OFFER', 'WAITLIST_PROMOTED', 'WAITLIST_EXPIRED',
    'SESSION_CHANGE', 'SESSION_CANCEL', 'REVIEW', 'ENTITLEMENT',
)


def _wx_access_token() -> str:
    url = ('https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential'
           '&appid=%s&secret=%s' % (settings.WX_APPID, settings.WX_SECRET))
    with urllib.request.urlopen(url, timeout=5) as r:
        j = json.loads(r.read().decode() or '{}')
    token = j.get('access_token', '')
    if not token:
        # 不把微信返回的原始报文（可能含敏感信息）写进异常
        raise RuntimeError('wx_access_token_unavailable')
    return token


def _send_subscribe_message(openid: str, title: str, content: str) -> tuple:
    """返回 (channel_status, detail)。任何失败都不向上抛，避免影响主业务事务。"""
    if not settings.WX_SUBSCRIBE_ENABLED:
        return 'SKIPPED_DISABLED', '订阅消息通道未开启（WX_SUBSCRIBE_ENABLED=false）'
    if not settings.WX_SUBSCRIBE_TEMPLATE_ID:
        return 'SKIPPED_NO_TEMPLATE', '未配置订阅消息模板 ID（微信公众平台资质待申请）'
    if not settings.WX_APPID or not settings.WX_SECRET:
        return 'SKIPPED_NO_CREDENTIAL', '未配置 WX_APPID / WX_SECRET'
    if not openid:
        return 'SKIPPED_NO_OPENID', '会员未绑定 openid'
    try:
        token = _wx_access_token()
        url = ('https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=' + token)
        # 模板 data 字段需按公众平台实际申请的模板 key 调整；此处使用通用 thing 字段占位。
        payload = {
            'touser': openid,
            'template_id': settings.WX_SUBSCRIBE_TEMPLATE_ID,
            'page': 'pages/mybookings/mybookings',
            'data': {
                'thing1': {'value': title[:20]},
                'thing2': {'value': content[:20]},
            },
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=5) as r:
            j = json.loads(r.read().decode() or '{}')
        if j.get('errcode') == 0:
            return 'SENT', '微信订阅消息已受理'
        return 'FAILED', '微信返回 errcode=%s' % j.get('errcode')
    except Exception as e:
        # 只记录异常类型，避免把 URL / token / 报文写进日志或数据库
        return 'FAILED', 'subscribe_send_failed:%s' % type(e).__name__


def notify(db: Session, tenant_id: str, member_id: str, title: str, content: str,
           scene: str, ref_id: str = '', mtype: str = 'BOOKING') -> Dict:
    """写站内信，并尝试订阅消息通道；返回通道状态供调用方与 QA 核对。"""
    member = db.get(Member, member_id) if member_id else None
    if member and member.tenant_id != tenant_id:
        member = None
    status, detail = _send_subscribe_message(
        member.openid if member else '', title, content)
    msg = InAppMessage(
        id=random_id(), tenant_id=tenant_id, member_id=member_id,
        title=title, content=content, mtype=mtype, ref_id=ref_id,
        scene=scene, channel_status=status, channel_detail=detail[:240],
    )
    db.add(msg)
    return {'id': msg.id, 'scene': scene, 'channel_status': status,
            'channel_detail': detail, 'in_app': True}
