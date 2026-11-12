#!/bin/sh
# 启动编排（T-35）：等库就绪 → 执行迁移 → 起服务。
# 重复执行幂等：alembic upgrade head 无待应用迁移时为空操作。
set -eu

: "${DATABASE_URL:?DATABASE_URL 未配置}"

echo "[entrypoint] 等待数据库就绪 ..."
i=0
until python - <<'PY'
import os, sys
from sqlalchemy import create_engine, text
url = os.environ["DATABASE_URL"]
try:
    eng = create_engine(url, pool_pre_ping=True)
    with eng.connect() as c:
        c.execute(text("SELECT 1"))
except Exception as exc:
    print("  db not ready:", type(exc).__name__, file=sys.stderr)
    sys.exit(1)
PY
do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "[entrypoint] 数据库 30 次重试仍不可用，退出" >&2
    exit 1
  fi
  sleep 2
done

echo "[entrypoint] 执行数据库迁移 ..."
alembic upgrade head

echo "[entrypoint] 迁移完成，当前版本：$(alembic current 2>/dev/null | tail -1)"

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[entrypoint] 警告：SEED_DEMO=true，将写入演示数据（生产环境请设为 false）" >&2
fi

echo "[entrypoint] 启动 uvicorn（workers=${WORKERS:-2}）"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8002 \
  --workers "${WORKERS:-2}" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --no-access-log
