"""会员服务：建档、租户内唯一、批量导入、跟进。"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BusinessRuleError, Conflict, NotFound, ValidationError
from ..models import FollowUp, Member, MemberTag
from ..repositories.base import write_audit
from ..security import mask_phone, phone_hash, random_id


def create_member(db: Session, tenant_id: str, operator: Dict, data: dict) -> Member:
    phone = (data.get('phone') or '').strip()
    name = (data.get('name') or '').strip()
    if not phone or len(phone) < 7:
        raise ValidationError('手机号不合法')
    if not name:
        raise ValidationError('姓名不能为空')
    ph = phone_hash(phone)
    exists = db.scalar(select(Member).where(Member.tenant_id == tenant_id, Member.phone_hash == ph))
    if exists:
        raise Conflict('该手机号会员已存在（租户内唯一）')
    m = Member(id=random_id(), tenant_id=tenant_id, phone_hash=ph, phone_mask=mask_phone(phone),
               name=name, source_store_id=data.get('source_store_id', ''), note=data.get('note', ''))
    db.add(m)
    write_audit(db, tenant_id, operator['id'], operator['name'], 'CREATE', 'member', m.id, {'name': name})
    return m


def list_members(db: Session, tenant_id: str, keyword: str = '', limit: int = 100) -> List[Member]:
    stmt = select(Member).where(Member.tenant_id == tenant_id).order_by(Member.created_at.desc()).limit(limit)
    if keyword:
        like = '%' + keyword + '%'
        stmt = stmt.where(Member.name.like(like) | Member.phone_mask.like(like))
    return list(db.scalars(stmt).all())


def get_member(db: Session, tenant_id: str, member_id: str) -> Member:
    m = db.get(Member, member_id)
    if not m or m.tenant_id != tenant_id:
        raise NotFound('会员不存在')
    return m


def update_member(db: Session, tenant_id: str, operator: Dict, member_id: str, data: dict) -> Member:
    m = get_member(db, tenant_id, member_id)
    if 'name' in data and data['name']:
        m.name = data['name']
    if 'note' in data:
        m.note = data['note']
    if 'status' in data:
        m.status = data['status']
    write_audit(db, tenant_id, operator['id'], operator['name'], 'UPDATE', 'member', m.id, data)
    return m


def import_members(db: Session, tenant_id: str, operator: Dict, rows: List[dict]) -> dict:
    """批量导入：逐行校验，返回成功/失败明细，不整批失败。"""
    ok, failed = [], []
    seen = set()
    for i, row in enumerate(rows):
        phone = (row.get('phone') or '').strip()
        name = (row.get('name') or '').strip()
        idx = i + 1
        if not phone or len(phone) < 7:
            failed.append({'row': idx, 'reason': '手机号不合法'})
            continue
        ph = phone_hash(phone)
        if ph in seen or db.scalar(select(Member).where(
                Member.tenant_id == tenant_id, Member.phone_hash == ph)):
            failed.append({'row': idx, 'phone': mask_phone(phone), 'reason': '手机号重复'})
            continue
        if not name:
            failed.append({'row': idx, 'phone': mask_phone(phone), 'reason': '姓名为空'})
            continue
        seen.add(ph)
        m = Member(id=random_id(), tenant_id=tenant_id, phone_hash=ph, phone_mask=mask_phone(phone),
                   name=name, source_store_id=row.get('source_store_id', ''), note=row.get('note', ''))
        db.add(m)
        ok.append({'row': idx, 'name': name})
    write_audit(db, tenant_id, operator['id'], operator['name'], 'IMPORT', 'member', '',
                {'ok': len(ok), 'failed': len(failed)})
    return {'ok_count': len(ok), 'failed_count': len(failed), 'ok': ok, 'failed': failed}


# ---------- 跟进 ----------
def add_follow_up(db: Session, tenant_id: str, operator: Dict, member_id: str, data: dict) -> FollowUp:
    get_member(db, tenant_id, member_id)
    f = FollowUp(id=random_id(), tenant_id=tenant_id, member_id=member_id,
                 coach_id=operator['id'], content=data.get('content', ''),
                 visible_to_member=bool(data.get('visible_to_member', False)),
                 created_by=operator['id'])
    db.add(f)
    return f


def list_follow_ups(db: Session, tenant_id: str, member_id: str) -> List[FollowUp]:
    return list(db.scalars(select(FollowUp).where(
        FollowUp.tenant_id == tenant_id, FollowUp.member_id == member_id)
        .order_by(FollowUp.created_at.desc())).all())
