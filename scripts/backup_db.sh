#!/usr/bin/env bash
# Codzienny backup bazy PostgreSQL cyrk_na_szynach.
# Zapis do backups/ w formacie custom pg_dump (skompresowany, obsługuje pg_restore).
# Zachowuje ostatnie 7 dni; starsze pliki są usuwane automatycznie.
#
# Użycie ręczne:
#   bash scripts/backup_db.sh
#
# Przywracanie:
#   docker exec -i cyrk-na-szynach-db pg_restore \
#     -U cyrk_na_szynach -d cyrk_na_szynach --clean < backups/<plik>.dump

set -euo pipefail

CONTAINER="cyrk-na-szynach-db"
DB_USER="cyrk_na_szynach"
DB_NAME="cyrk_na_szynach"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/../backups"
LOG_FILE="$BACKUP_DIR/backup.log"
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

DATE=$(date +%Y-%m-%d_%H%M)
FILENAME="$BACKUP_DIR/cyrk_${DATE}.dump"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Backup start ==="

if ! docker ps --filter "name=^${CONTAINER}$" --filter "status=running" --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    log "BŁĄD: Kontener $CONTAINER nie działa."
    exit 1
fi

docker exec "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$FILENAME"

SIZE=$(du -h "$FILENAME" | cut -f1)
log "Backup zapisany: $(basename "$FILENAME") ($SIZE)"

# Usuń pliki starsze niż KEEP_DAYS dni (pomijaj backup.log)
DELETED=$(find "$BACKUP_DIR" -name "cyrk_*.dump" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "Usunięto $DELETED starych backup(ów) (> ${KEEP_DAYS} dni)"
fi

log "=== Backup OK ==="
