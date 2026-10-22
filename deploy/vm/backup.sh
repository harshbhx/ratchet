#!/usr/bin/env bash
# Back up the SQLite ledger from the running ratchet container to a dated file on the VM,
# and optionally to Nebius Object Storage.
#   /opt/ratchet/deploy/vm/backup.sh            (cloud-init runs it daily as the ratchet user)
#
# Uses SQLite's online backup API inside the container (what `sqlite3 db ".backup file"` does),
# so the copy is consistent while the gateway keeps writing.
# Mock mode keeps the ledger in memory: there is nothing to back up and the script exits 0.
#
# Env (read from deploy/vm/.env unless already set):
#   RATCHET_DB            ledger path inside the container (default /data/ratchet.db)
#   RATCHET_BACKUP_DIR    host folder (default /var/backups/ratchet)
#   RATCHET_BACKUP_KEEP   local copies to keep (default 14)
#   RATCHET_BUCKET_S3     s3://bucket/prefix: also upload there with the aws CLI
#   RATCHET_S3_ENDPOINT   Object Storage S3 endpoint (copy it from the console; verify on day 1)
#   RATCHET_BACKUP_TAG    letters, digits and "-" (up to 32): ratchet-<stamp>-<tag>.db.gz (update.sh uses "pre-update")
# The last line printed is backup_file=<path>, which update.sh reads (restore with deploy/vm/restore.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_get() { grep -E "^$1=" "$HERE/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }

MODE="${RATCHET_MODE:-$(env_get RATCHET_MODE)}"
DB="${RATCHET_DB:-$(env_get RATCHET_DB)}"; DB="${DB:-/data/ratchet.db}"
OUT_DIR="${RATCHET_BACKUP_DIR:-/var/backups/ratchet}"
KEEP="${RATCHET_BACKUP_KEEP:-14}"
BUCKET="${RATCHET_BUCKET_S3:-$(env_get RATCHET_BUCKET_S3)}"
S3_ENDPOINT="${RATCHET_S3_ENDPOINT:-$(env_get RATCHET_S3_ENDPOINT)}"
TAG="${RATCHET_BACKUP_TAG:-}"
if [[ -n "$TAG" && ! "$TAG" =~ ^[A-Za-z0-9-]{1,32}$ ]]; then
  echo "RATCHET_BACKUP_TAG must be 1 to 32 letters, digits or -" >&2
  exit 1
fi

if [[ "${MODE:-mock}" == "mock" ]]; then
  echo "$(date -u +%FT%TZ) mock mode: ledger is in memory, nothing to back up"
  exit 0
fi

cd "$HERE"
[[ -n "$(docker compose ps -q ratchet)" ]] || { echo "ratchet container is not running" >&2; exit 1; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="ratchet-${stamp}${TAG:+-$TAG}.db"
tmp_in_container="/data/.backup-${stamp}.db"
mkdir -p "$OUT_DIR"

# 1. Online backup + integrity check inside the container (the image has Python but no sqlite3 CLI).
docker compose exec -T ratchet python - "$DB" "$tmp_in_container" <<'PY'
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
if not os.path.exists(src):
    sys.exit(f"ledger not found: {src}")
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
ok = d.execute("PRAGMA integrity_check").fetchone()[0]
d.close(); s.close()
if ok != "ok":
    sys.exit(f"integrity_check failed: {ok}")
PY

# 2. Copy it out, remove the temporary copy, compress.
docker compose cp "ratchet:${tmp_in_container}" "${OUT_DIR}/${name}"
docker compose exec -T ratchet rm -f "$tmp_in_container"
gzip -f "${OUT_DIR}/${name}"
echo "$(date -u +%FT%TZ) wrote ${OUT_DIR}/${name}.gz ($(du -h "${OUT_DIR}/${name}.gz" | cut -f1))"

# 3. Keep the newest $KEEP local copies.
find "$OUT_DIR" -maxdepth 1 -name 'ratchet-*.db.gz' -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f

# 4. Optional upload to Nebius Object Storage (S3-compatible). Needs the aws CLI on the VM
#    (sudo snap install aws-cli --classic) and an access key for the bucket (aws configure, as this user).
if [[ -n "$BUCKET" ]]; then
  command -v aws >/dev/null || { echo "RATCHET_BUCKET_S3 is set but the aws CLI is missing" >&2; exit 1; }
  aws s3 cp "${OUT_DIR}/${name}.gz" "${BUCKET%/}/backups/${name}.gz" ${S3_ENDPOINT:+--endpoint-url "$S3_ENDPOINT"}
  echo "$(date -u +%FT%TZ) uploaded to ${BUCKET%/}/backups/${name}.gz"
fi
echo "backup_file=${OUT_DIR}/${name}.gz"
