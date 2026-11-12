"""教练评价（FR-20）：先审后显、敏感信息拦截、一课一评。

- 会员仅可评价自己「已到课」的课程，且同一课程只能评价一次（唯一约束兜底）；
- 提交后为 PENDING，会员端与公开列表只可见 PUBLISHED；
- 含手机号 / 身份证号等个人信息的评价直接拦截，不落库、不进入审核队列；
- 店长及以上可发布或隐藏违规评价，操作写审计。
"""
from __future__ import annotations

import re
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict, NotFound, ValidationError
from ..models import ClassSession, CoachReview, Enrollment, Member
from ..repositories.base import write_audit
from ..security import random_id
from ..utils.timeutil import now_naive

_PHONE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
_IDCARD_RE = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
MAX_CONTENT = 300


def _get_session(db: Session, tenant_id: str, session_id: str) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s or s.tenant_id != tenant_id:
        raise NotFound('课节不存在')
    return s


def submit_review(db: Session, tenant_id: str, member_id: str, data: dict) -> CoachReview:
    session_id = (data.get('session_id') or '').strip()
    if not session_id:
        raise ValidationError('课程不能为空')
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        raise ValidationError('评分必须为 1-5 的整数')
    if rating < 1 or rating > 5:
        raise ValidationError('评分必须为 1-5 星')
    content = (data.get('content') or '').strip()
    if len(content) > MAX_CONTENT:
        raise ValidationError('评价内容不能超过 %d 字' % MAX_CONTENT)
    if _PHONE_RE.search(content) or _IDCARD_RE.search(content):
        raise ValidationError('评价包含手机号等个人信息，请修改后再提交')
    # 仅已到课可评价
    enr = db.scalar(select(Enrollment).where(
        Enrollment.tenant_id == tenant_id, Enrollment.session_id == session_id,
        Enrollment.member_id == member_id, Enrollment.status == 'ATTENDED'))
    if not enr:
        raise BusinessRuleError('仅已到课的课程可以评价教练')
    dup = db.scalar(select(CoachReview).where(
        CoachReview.tenant_id == tenant_id, CoachReview.session_id == session_id,
        CoachReview.member_id == member_id))
    if dup:
        raise Conflict('该课程已提交过评价')
    s = _get_session(db, tenant_id, session_id)
    r = CoachReview(id=random_id(), tenant_id=tenant_id, session_id=session_id,
                    member_id=member_id, coach_id=s.coach_id, rating=rating,
                    content=content, status='PENDING')
    db.add(r)
    write_audit(db, tenant_id, member_id, '会员', 'SUBMIT_REVIEW', 'coach_review', r.id,
                {'session_id': session_id, 'rating': rating})
    return r


def _mask_name(name: str) -> str:
    return (name[:1] + '**') if name else '匿名'


def list_public(db: Session, tenant_id: str, coach_id: str = '') -> List[Dict]:
    stmt = select(CoachReview).where(
        CoachReview.tenant_id == tenant_id, CoachReview.status == 'PUBLISHED')
    if coach_id:
        stmt = stmt.where(CoachReview.coach_id == coach_id)
    rows = db.scalars(stmt.order_by(CoachReview.created_at.desc()).limit(100)).all()
    out = []
    for r in rows:
        m = db.get(Member, r.member_id)
        out.append({
            'id': r.id, 'session_id': r.session_id, 'coach_id': r.coach_id,
            'rating': r.rating, 'content': r.content,
            'member_name': _mask_name(m.name if m else ''),
            'created_at': r.created_at.strftime('%Y-%m-%d'),
        })
    return out


def list_all(db: Session, tenant_id: str, status: str = '') -> List[Dict]:
    stmt = select(CoachReview).where(CoachReview.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(CoachReview.status == status)
    rows = db.scalars(stmt.order_by(CoachReview.created_at.desc()).limit(200)).all()
    out = []
    for r in rows:
        m = db.get(Member, r.member_id)
        out.append({
            'id': r.id, 'session_id': r.session_id, 'coach_id': r.coach_id,
            'member_name': _mask_name(m.name if m else ''),
            'rating': r.rating, 'content': r.content, 'status': r.status,
            'reject_reason': r.reject_reason,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
        })
    return out


def moderate(db: Session, tenant_id: str, operator: Dict, review_id: str,
             action: str, reason: str = '') -> Dict:
    r = db.get(CoachReview, review_id)
    if not r or r.tenant_id != tenant_id:
        raise NotFound('评价不存在')
    action = (action or '').upper()
    if action not in ('PUBLISH', 'HIDE'):
        raise ValidationError('审核动作非法')
    if r.status != 'PENDING':
        raise Conflict('该评价已审核，不可重复处理')
    r.status = 'PUBLISHED' if action == 'PUBLISH' else 'HIDDEN'
    r.reject_reason = (reason or '')[:240]
    r.moderated_by = operator['id']
    r.moderated_at = now_naive()
    write_audit(db, tenant_id, operator['id'], operator['name'], 'MODERATE_REVIEW',
                'coach_review', r.id, {'action': action, 'reason': r.reject_reason})
    return {'id': r.id, 'status': r.status}
