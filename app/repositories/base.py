"""Repository 基类：所有查询强制 tenant_id 收口，越权零容忍；附审计写入工具。"""
from __future__ import annotations

import json
from typing import Any, Generic, List, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound, TenantViolation
from ..models import AuditLog
from ..models.base import Base
from ..security import random_id

T = TypeVar('T', bound=Base)


class BaseRepository(Generic[T]):
    model: Type[T]

    def __init__(self, db: Session, tenant_id: str = ''):
        self.db = db
        self.tenant_id = tenant_id

    @property
    def _has_tenant(self) -> bool:
        return hasattr(self.model, 'tenant_id')

    def _scoped(self, stmt):
        if self._has_tenant:
            stmt = stmt.where(self.model.tenant_id == self.tenant_id)
        return stmt

    def get(self, obj_id: str) -> T:
        obj = self.db.get(self.model, obj_id)
        if obj is None:
            raise NotFound('%s 不存在' % self.model.__tablename__)
        return self._assert_tenant(obj)

    def _assert_tenant(self, obj: T) -> T:
        if self._has_tenant and getattr(obj, 'tenant_id') != self.tenant_id:
            raise TenantViolation()
        return obj

    def list(self, **filters) -> List[T]:
        stmt = select(self.model)
        for k, v in filters.items():
            stmt = stmt.where(getattr(self.model, k) == v)
        stmt = self._scoped(stmt)
        return list(self.db.scalars(stmt).all())

    def create(self, **fields) -> T:
        if self._has_tenant:
            fields.setdefault('tenant_id', self.tenant_id)
        obj = self.model(id=random_id(), **fields)
        self.db.add(obj)
        return obj

    def save(self, obj: T) -> T:
        self._assert_tenant(obj)
        self.db.add(obj)
        return obj

    def delete(self, obj: T) -> None:
        self._assert_tenant(obj)
        self.db.delete(obj)


def write_audit(
    db: Session,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    action: str,
    resource: str,
    resource_id: str = '',
    detail: Any = None,
    ip: str = '',
) -> None:
    """关键写操作审计留痕，随当前事务提交。"""
    db.add(AuditLog(
        id=random_id(),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        resource=resource,
        resource_id=resource_id,
        detail=json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else '',
        ip=ip,
    ))
