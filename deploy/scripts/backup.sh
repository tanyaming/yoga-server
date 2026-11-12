#!/usr/bin/env bash
# 每日备份（T-33）：mysqldump 一致性快照 + gzip + 校验和 + 保留 N 天
# 建议 crontab：0 2 * * * /opt/yoga-server/deploy/scripts/backup.sh >> /var/log/yoga-backup.log 2>&1
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-$(cd "$(dirname "$0")/.." && pwd)/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
DB_NAME="${MYSQL_DATABASE:-yoga}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/${DB_NAME}-${STAMP}.sql.gz"

echo "[backup] $(date '+%F %T') 开始导出 $DB_NAME"

# --single-transaction：InnoDB 一致性快照，不锁表
docker compose -f "$COMPOSE_FILE" exec -T mysql \
  mysqldump --single-transaction --quick --routines --triggers \
  -u root -p"${MYSQL_ROOT_PASSWORD:?需要 MYSQL_ROOT_PASSWORD 环境变量}" "$DB_NAME" \
  | gzip -9 > "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
SHA=$(shasum -a 256 "$OUT" 2>/dev/null | cut -d' ' -f1 || sha256sum "$OUT" | cut -d' ' -f1)
echo "$SHA  $(basename "$OUT")" >> "$BACKUP_DIR/checksums.txt"

# 备份可用性前置校验：空文件或过小视为失败
BYTES=$(wc -c < "$OUT" | tr -d ' ')
if [ "$BYTES" -lt 1024 ]; then
  echo "[backup] 失败：产物仅 ${BYTES} 字节，疑似导出异常" >&2
  exit 1
fi

echo "[backup] 完成 $OUT（$SIZE, sha256=${SHA:0:16}…）"

# 保留策略
find "$BACKUP_DIR" -name "${DB_NAME}-*.sql.gz" -type f -mtime "+$KEEP_DAYS" -print -delete
echo "[backup] 已清理超过 ${KEEP_DAYS} 天的备份"
