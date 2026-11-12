"""排课 / 约课路由（后台，店长及以上）。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import AuthUser, require_min_role
from ..services import schedule as svc

router = APIRouter(prefix='/api/schedule', tags=['schedule'])

mgr = require_min_role('STORE_MANAGER')
# 教练只有排课页的只读权限（T-27）：读接口放宽到 COACH，写接口仍是店长及以上
viewer = require_min_role('COACH')


class TemplateBody(BaseModel):
    name: str = Field(min_length=1)
    store_id: str = ''
    coach_id: str = ''
    ctype: str = 'GROUP'
    capacity: int = 15
    duration_minutes: int = 60
    allowed_ptypes: str = ''          # 可约卡种 PACKAGE/PRIVATE/STORED，逗号分隔，空=不限


class TemplateUpdateBody(BaseModel):
    """模板更新：字段全可选，未传即保持原值。"""
    name: Optional[str] = None
    code: Optional[str] = None
    ctype: Optional[str] = None
    capacity: Optional[int] = None
    duration_minutes: Optional[int] = None
    store_id: Optional[str] = None
    coach_id: Optional[str] = None
    allowed_ptypes: Optional[str] = None


class SessionBody(BaseModel):
    template_id: str = Field(min_length=1)
    starts_at: str = Field(min_length=1)
    cancel_deadline_hours: int = 2


class WeeklyBody(BaseModel):
    template_id: str = Field(min_length=1)
    week_start: str = Field(min_length=1)              # YYYY-MM-DD（周一）
    times: List[str] = Field(default_factory=list)     # ['09:00', '19:30']
    # 生成哪几天：1=周一 … 7=周日；不传=整周（向后兼容旧调用方）
    days: Optional[List[int]] = None
    cancel_deadline_hours: int = 2

    @field_validator('days', mode='before')
    @classmethod
    def _reject_bool_days(cls, value):
        """``true`` / ``"1"`` 都不能当星期用。

        两处静默纠错必须堵死：
        - Python 里 ``True == 1``，pydantic 宽松模式会把 JSON 的 ``true`` 转成 1（周一）；
        - ``"1"`` 这类字符串也会被宽松模式转成 1，前端传错类型就变成「悄悄只排一天」。
        统一换成 0（非法星期），交给服务层用统一文案 400「请选择 1–7 之间的星期」。
        """
        if isinstance(value, list):
            return [0 if isinstance(item, (bool, str)) else item for item in value]
        return value


# ---------- 课程模板 ----------
@router.get('/templates')
def list_templates(store_id: str = '', auth: AuthUser = Depends(viewer),
                   db: Session = Depends(get_db)):
    return [{'id': t.id, 'code': t.code, 'name': t.name, 'store_id': t.store_id,
             'coach_id': t.coach_id, 'ctype': t.ctype, 'capacity': t.capacity,
             'duration_minutes': t.duration_minutes, 'allowed_ptypes': t.allowed_ptypes}
            for t in svc.list_templates(db, auth.tenant_id, store_id)]


@router.post('/templates')
def create_template(body: TemplateBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    t = svc.create_template(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': t.id, 'name': t.name, 'allowed_ptypes': t.allowed_ptypes}


@router.patch('/templates/{template_id}')
def update_template(template_id: str, body: TemplateUpdateBody,
                    auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    """T-29：改课程模板。存量课节不受影响，返回未来课节数供前端提示。"""
    t, affected = svc.update_template(db, auth.tenant_id, _op(auth), template_id,
                                      body.model_dump())
    db.commit()
    return {'id': t.id, 'code': t.code, 'name': t.name, 'store_id': t.store_id,
            'coach_id': t.coach_id, 'ctype': t.ctype, 'capacity': t.capacity,
            'duration_minutes': t.duration_minutes, 'allowed_ptypes': t.allowed_ptypes,
            'affected_future_sessions': affected}


@router.get('/templates/{template_id}')
def template_detail(template_id: str, auth: AuthUser = Depends(viewer),
                    db: Session = Depends(get_db)):
    t = svc.get_template(db, auth.tenant_id, template_id)
    return {'id': t.id, 'code': t.code, 'name': t.name, 'store_id': t.store_id,
            'coach_id': t.coach_id, 'ctype': t.ctype, 'capacity': t.capacity,
            'duration_minutes': t.duration_minutes, 'allowed_ptypes': t.allowed_ptypes}


# ---------- 排期 ----------
@router.post('/weekly')
def generate_weekly(body: WeeklyBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    """FR-11：按周批量生成排期（幂等，重复调用不产生重复课节）。

    ``days`` 可选：不传=整周（兼容旧客户端），传 [1,3,5] 只生成周一/三/五。
    """
    rows = svc.generate_weekly_sessions(db, auth.tenant_id, _op(auth), body.template_id,
                                        body.week_start, body.times,
                                        body.cancel_deadline_hours, body.days)
    db.commit()
    # skipped 口径必须与服务层一致：星期数 × 去重后的时段数 - 实际新建，
    # 否则 times=['9:00','09:00'] / days 重复 这类等价写法会让「跳过数」虚高。
    day_indexes = svc.normalize_days(body.days)
    slots = len(day_indexes) * len(svc.parse_weekly_times(body.times))
    # G6：week_start 允许传任意日期，实际生效的是该周周一，两者都回显便于前端提示与排查
    effective = svc.normalize_week_start(body.week_start).strftime('%Y-%m-%d')
    return {'created': len(rows), 'skipped': max(0, slots - len(rows)),
            'days': day_indexes,
            'week_start': effective,
            'requested_week_start': (body.week_start or '').strip()[:10],
            'session_ids': [s.id for s in rows]}


@router.get('/sessions')
def list_sessions(store_id: str = '', date: str = '', coach_id: str = '',
                  auth: AuthUser = Depends(viewer), db: Session = Depends(get_db)):
    rows = svc.list_sessions(db, auth.tenant_id, store_id, date, coach_id)
    return [{'id': s.id, 'course_name': s.course_name, 'store_id': s.store_id,
             'coach_id': s.coach_id, 'starts_at': s.starts_at.strftime('%Y-%m-%d %H:%M'),
             'ends_at': s.ends_at.strftime('%H:%M'), 'capacity': s.capacity,
             'booked': s.booked, 'status': s.status} for s in rows]


@router.post('/sessions')
def create_session(body: SessionBody, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    s = svc.create_session(db, auth.tenant_id, _op(auth), body.model_dump())
    db.commit()
    return {'id': s.id, 'course_name': s.course_name}


@router.post('/sessions/{session_id}/cancel')
def cancel_session(session_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    s = svc.cancel_session(db, auth.tenant_id, _op(auth), session_id)
    db.commit()
    return {'id': s.id, 'status': s.status}


@router.get('/sessions/{session_id}')
def session_detail(session_id: str, auth: AuthUser = Depends(mgr), db: Session = Depends(get_db)):
    return svc.session_detail(db, auth.tenant_id, session_id)


def _op(auth: AuthUser) -> dict:
    return {'id': auth.id, 'name': auth.name}
