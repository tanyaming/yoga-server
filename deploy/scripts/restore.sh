#!/usr/bin/env bash
# 恢复（T-33）：从备份还原到 MySQL，必须二次确认；恢复后校验迁移版本与表数量。
# 用法：./restore.sh --confirm backups/yoga-20261112-020000.sql.gz
set -euo pipefail

if [ "${1:-}" != "--confirm" ] || [ -z "${2:-}" ]; then
  echo "用法：$0 --confirm <备份文件.sql.gz>" >&2
  echo "注意：恢复会覆盖当前数据库数据，请先确认已停止写入。" >&2
  exit 2
fi

FILE="$2"
[ -f "$FILE" ] || { echo "备份文件不存在：$FILE" >&2; exit 1; }

COMPOSE_FILE="${COMPOSE_FILE:-$(cd "$(dirname "$0")/.." && pwd)/docker-compose.yml}"
DB_NAME="${MYSQL_DATABASE:-yoga}"

echo "[restore] 目标库：$DB_NAME　来源：$FILE"
echo "[restore] 5 秒后开始，Ctrl-C 可中止 ..."
sleep 5

gunzip -c "$FILE" | docker compose -f "$COMPOSE_FILE" exec -T mysql \
  mysql -u root -p"${MYSQL_ROOT_PASSWORD:?需要 MYSQL_ROOT_PASSWORD 环境变量}" "$DB_NAME"

echo "[restore] 数据导入完成，校验迁移版本与表数量 ..."
docker compose -f "$COMPOSE_FILE" exec -T mysql \
  mysql -u root -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT CONCAT('alembic_version=', version_num) FROM $DB_NAME.alembic_version;
   SELECT CONCAT('tables=', COUNT(*)) FROM information_schema.tables WHERE table_schema='$DB_NAME';"

echo "[restore] 完成。请随后执行："
echo "  docker compose -f $COMPOSE_FILE restart app"
echo "  curl -fsS http://127.0.0.1:8002/health"
