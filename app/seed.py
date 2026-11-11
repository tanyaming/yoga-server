"""开发种子数据：幂等，走完整业务服务流程，供前端演示与冒烟测试。

固定 ID：tenant-suxu / store-jingan / store-pudong / user-admin / user-coach /
member-linxiao / member-suqing / member-wanglei。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .config import settings
from .database import session_scope
from .logging_config import get_logger
from .models import (ClassSession, CourseTemplate, Entitlement, Member, Product, Store,
                     Tenant, User)
from .security import hash_password, mask_phone, phone_hash
from .services import payment as pay_svc
from .services import schedule as sch_svc
from .utils.timeutil import business_date, now_naive, parse_dt

logger = get_logger('app.seed')


def _admin_password() -> str:
    pwd = settings.BOOTSTRAP_ADMIN_PASSWORD
    if not pwd or pwd == 'change-me':
        return 'admin123'
    return pwd


def seed_demo() -> None:
    with session_scope() as db:
        # 租户
        tenant = db.scalar(select(Tenant).where(Tenant.code == 'suxu'))
        if not tenant:
            tenant = Tenant(id='tenant-suxu', code='suxu', name='素序瑜伽', plan='STANDARD',
                            contact_name='林馆主', contact_phone_mask='138****8888')
            db.add(tenant)
            db.flush()

        # 门店
        for sid, code, name, addr in [
            ('store-jingan', 'JA', '静安店', '上海市静安区南京西路 1266 号'),
            ('store-pudong', 'PD', '浦东店', '上海市浦东新区世纪大道 100 号'),
        ]:
            if not db.get(Store, sid):
                db.add(Store(id=sid, tenant_id=tenant.id, code=code, name=name, address=addr,
                             business_hours='08:00-22:00'))

        # 账号
        if not db.get(User, 'user-admin'):
            db.add(User(id='user-admin', tenant_id=tenant.id, username='admin',
                        password_hash=hash_password(_admin_password()), real_name='林馆主',
                        role='TENANT_ADMIN'))
        if not db.get(User, 'user-coach'):
            db.add(User(id='user-coach', tenant_id=tenant.id, username='coach',
                        password_hash=hash_password('coach123'), real_name='陈屿',
                        role='COACH', store_id='store-jingan'))

        # 会员
        for mid, phone, name, store in [
            ('member-linxiao', '13800000001', '林晓', 'store-jingan'),
            ('member-suqing', '13800000002', '苏青', 'store-jingan'),
            ('member-wanglei', '13800000003', '王磊', 'store-pudong'),
        ]:
            if not db.get(Member, mid):
                db.add(Member(id=mid, tenant_id=tenant.id, phone_hash=phone_hash(phone),
                              phone_mask=mask_phone(phone), name=name, source_store_id=store))

        # 商品
        for pid, code, name, ptype, sessions, cents, days, cross in [
            ('product-pkg10', 'PKG10', '10 节团课包', 'PACKAGE', 10, 199900, 365, True),
            ('product-prv20', 'PRV20', '私教 20 节', 'PRIVATE', 20, 399900, 365, False),
            ('product-stored', 'STO500', '储值卡 500 元', 'STORED', 0, 50000, 0, True),
        ]:
            if not db.get(Product, pid):
                db.add(Product(id=pid, tenant_id=tenant.id, code=code, name=name, ptype=ptype,
                               total_sessions=sessions, price_cents=cents, valid_days=days,
                               cross_store=cross))

        # 课程模板
        tmpl = None
        if not db.scalar(select(CourseTemplate).where(
                CourseTemplate.tenant_id == tenant.id, CourseTemplate.name == '普拉提塑形')):
            tmpl = CourseTemplate(id='tmpl-pilates', tenant_id=tenant.id, code='PILATES',
                                  name='普拉提塑形', store_id='store-jingan', coach_id='user-coach',
                                  ctype='GROUP', capacity=8, duration_minutes=60)
            db.add(tmpl)
        else:
            tmpl = db.scalar(select(CourseTemplate).where(
                CourseTemplate.tenant_id == tenant.id, CourseTemplate.name == '普拉提塑形'))

        # 给林晓购买 10 节团课包（走完整收款流程）
        db.flush()
        admin_op = {'id': 'user-admin', 'name': '林馆主'}
        has_ent = db.scalar(select(Entitlement).where(
            Entitlement.tenant_id == tenant.id, Entitlement.member_id == 'member-linxiao'))
        if not has_ent:
            pay_svc.register_payment(db, tenant.id, admin_op, {
                'store_id': 'store-jingan', 'member_id': 'member-linxiao',
                'product_id': 'product-pkg10', 'amount_cents': 199900, 'method': 'WECHAT',
                'paid_at': now_naive().strftime('%Y-%m-%d %H:%M'),
                'idempotency_key': 'seed-pay-linxiao-pkg10',
            })

        # 排课：未来时段（相对当前时间偏移，保证任何时刻启动都可约可点名）
        offsets = {
            'session-today-1': timedelta(hours=2),
            'session-today-2': timedelta(hours=5),
            'session-tomorrow-1': timedelta(days=1),
        }
        for sid, name, cap in [
            ('session-today-1', '普拉提塑形', 8),
            ('session-today-2', '流瑜伽', 12),
            ('session-tomorrow-1', '普拉提塑形', 8),
        ]:
            if not db.get(ClassSession, sid):
                starts = (now_naive() + offsets[sid]).replace(minute=0, second=0, microsecond=0)
                db.add(ClassSession(
                    id=sid, tenant_id=tenant.id, template_id=tmpl.id if tmpl else '',
                    store_id='store-jingan', coach_id='user-coach', course_name=name,
                    starts_at=starts, ends_at=starts + timedelta(minutes=60), capacity=cap))

        # 林晓约今天上午的普拉提（用于点名演示）
        db.flush()
        from .models import Enrollment
        if not db.scalar(select(Enrollment).where(
                Enrollment.session_id == 'session-today-1', Enrollment.member_id == 'member-linxiao')):
            sch_svc.book(db, tenant.id, 'member-linxiao', 'session-today-1', 'MEMBER')

        logger.info('种子数据就绪', extra={'extra': {'tenant': tenant.name}})
