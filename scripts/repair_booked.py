#!/usr/bin/env python
"""课节超员数据修复脚本（T-04 配套）。

历史问题：并发约课用「读-改-写」维护 booked，容量=1 的课节可能出现多条 BOOKED。
本脚本把超员的预约降级为 WAITLIST（不删会员任何记录），并重算 booked。

用法：
    .venv/bin/python scripts/repair_booked.py --dry-run
    .venv/bin/python scripts/repair_booked.py --apply
    .venv/bin/python scripts/repair_booked.py --dry-run --database-url sqlite:////tmp/x.db

安全约束：
- 默认 dry-run，必须显式 --apply 才写库；
- --apply 前建议先做一次库备份（见 deploy/scripts/backup.sh）；
- 每条降级都写 AuditLog(action='DATA_REPAIR')，可按记录回溯与通知会员。
"""
from __future__ import annotations

import argparse
import os
import sys

# 以 `python scripts/repair_booked.py` 直接运行时，sys.path[0] 是 scripts/，
# 需要显式把仓库根目录加进来，否则 import app 会失败。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description='课节名额超员修复')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true', default=True,
                       help='只输出报表，不改数据（默认行为）')
    group.add_argument('--apply', action='store_true',
                       help='实际执行修复并写审计')
    parser.add_argument('--tenant-id', default='', help='只修复指定租户')
    parser.add_argument('--database-url', default='', help='覆盖 DATABASE_URL（便于在备份副本上演练）')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.database_url:
        os.environ['DATABASE_URL'] = args.database_url
    # 延迟导入：保证上面的 DATABASE_URL 覆盖在引擎创建前生效
    from app.database import session_scope
    from app.services.schedule_repair import (apply_repair, build_repair_plan,
                                             format_report)

    dry_run = not args.apply
    with session_scope() as db:
        plans = build_repair_plan(db, args.tenant_id)
        if not plans:
            print('未发现需要修复的课节（booked 与占位一致，且无超员）')
            return 0
        summary = apply_repair(db, plans, dry_run=dry_run)
        print(format_report(plans, summary))
        if dry_run:
            print('\n以上为 dry-run 结果，未写库。确认后执行：--apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
