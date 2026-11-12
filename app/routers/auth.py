"""认证路由：后台账号登录 / 刷新 / 退出，以及小程序会员登录与绑定。"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..dependencies import AuthUser, get_current_user
from ..errors import Unauthorized, ValidationError
from ..models import Member, RefreshToken, User
from ..security import (create_access_token, hash_refresh_token, mask_phone,
                        new_refresh_token, phone_hash, random_id, verify_password)
from ..utils.timeutil import now_naive
from ..utils.wechat import code2session

router = APIRouter(prefix='/api/auth', tags=['auth'])


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    tenant_id: Optional[str] = None


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class WxLoginBody(BaseModel):
    code: str = Field(min_length=1)
    tenant_id: Optional[str] = None


class BindBody(BaseModel):
    code: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    phone: str = Field(min_length=7)
    name: str = Field(min_length=1)


def _user_payload(user: User) -> Dict:
    return {
        'id': user.id, 'tenant_id': user.tenant_id, 'username': user.username,
        'real_name': user.real_name, 'role': user.role, 'store_id': user.store_id,
    }


@router.post('/login')
def login(body: LoginBody, db: Session = Depends(get_db)):
    stmt = select(User).where(User.username == body.username)
    if body.tenant_id:
        stmt = stmt.where(User.tenant_id == body.tenant_id)
    user = db.scalar(stmt)
    if not user or not verify_password(body.password, user.password_hash):
        raise Unauthorized('账号或密码错误')
    if user.status != 'ACTIVE':
        raise Unauthorized('账号已停用')
    user.last_login_at = now_naive()
    store_ids = [user.store_id] if user.store_id else []
    access = create_access_token(user.id, user.tenant_id, user.role, store_ids)
    refresh_raw, refresh_hash = new_refresh_token()
    db.add(RefreshToken(
        id=random_id(), tenant_id=user.tenant_id, user_id=user.id, token_hash=refresh_hash,
        expires_at=now_naive() + timedelta(days=settings.REFRESH_TOKEN_DAYS)))
    db.commit()
    return {'access_token': access, 'refresh_token': refresh_raw, 'user': _user_payload(user)}


@router.get('/me')
def me(auth: AuthUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前登录账号信息。

    前端刷新页面后 sessionStorage 里只有 token，必须能还原 role 才能收敛侧栏可见性
    （T-27）；这里只回显自身信息，不引入任何写操作。
    """
    user = db.get(User, auth.id)
    if not user or user.tenant_id != auth.tenant_id:
        raise Unauthorized('账号不存在或已停用')
    return _user_payload(user)


@router.post('/refresh')
def refresh(body: RefreshBody, db: Session = Depends(get_db)):
    h = hash_refresh_token(body.refresh_token)
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == h))
    if not tok or tok.revoked or tok.expires_at <= now_naive():
        raise Unauthorized('刷新令牌无效或已过期')
    user = db.get(User, tok.user_id)
    if not user or user.status != 'ACTIVE':
        raise Unauthorized('账号不存在或已停用')
    access = create_access_token(user.id, user.tenant_id, user.role,
                                 [user.store_id] if user.store_id else [])
    return {'access_token': access, 'user': _user_payload(user)}


@router.post('/logout')
def logout(body: RefreshBody, db: Session = Depends(get_db),
           _auth=Depends(get_current_user)):
    h = hash_refresh_token(body.refresh_token)
    tok = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == h))
    if tok:
        tok.revoked = True
        db.commit()
    return {'ok': True}


# ---------- 小程序会员 ----------
def _member_payload(m: Member) -> Dict:
    return {'id': m.id, 'name': m.name, 'phone_mask': m.phone_mask, 'tenant_id': m.tenant_id}


@router.post('/wx-login')
def wx_login(body: WxLoginBody, db: Session = Depends(get_db)):
    info = code2session(body.code)
    openid = info['openid']
    stmt = select(Member).where(Member.openid == openid)
    if body.tenant_id:
        stmt = stmt.where(Member.tenant_id == body.tenant_id)
    member = db.scalar(stmt)
    if member:
        token = create_access_token(member.id, member.tenant_id, 'MEMBER', [])
        return {'need_bind': False, 'token': token, 'member': _member_payload(member)}
    return {'need_bind': True, 'openid': openid}


@router.post('/wx-bind')
def wx_bind(body: BindBody, db: Session = Depends(get_db)):
    info = code2session(body.code)
    openid = info['openid']
    phone = body.phone.strip()
    if len(phone) < 7:
        raise ValidationError('手机号不合法')
    ph = phone_hash(phone)
    member = db.scalar(select(Member).where(
        Member.tenant_id == body.tenant_id, Member.phone_hash == ph))
    if member:
        if member.openid and member.openid != openid:
            raise ValidationError('该手机号已绑定其他微信')
        member.openid = openid
        if body.name:
            member.name = body.name
    else:
        member = Member(id=random_id(), tenant_id=body.tenant_id, phone_hash=ph,
                        phone_mask=mask_phone(phone), name=body.name, openid=openid)
        db.add(member)
    db.commit()
    token = create_access_token(member.id, member.tenant_id, 'MEMBER', [])
    return {'need_bind': False, 'token': token, 'member': _member_payload(member)}
