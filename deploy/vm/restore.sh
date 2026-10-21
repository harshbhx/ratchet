#!/usr/bin/env bash
# Restore the ledger from a backup made by backup.sh. The gateway container must be stopped.
#   docker compose stop ratchet && deploy/vm/restore.sh /var/backups/ratchet/ratchet-<stamp>.db.gz && docker compose start ratchet
#
# The replaced ledger is kept as /data/ratchet.db.before-restore-<stamp>, together with its -wal and -shm files: copying a
# backup over a database without moving its WAL aside can corrupt it (https://www.sqlite.org/howtocorrupt.html, 1.4).
# Uses only the Python standard library inside the image, so it works with any image version.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
file="${1:-}"
[[ -f "$file" ]] || { echo "usage: restore.sh <backup.db.gz>" >&2; exit 2; }
cd "$HERE"
running="$(docker compose ps -q --status running ratchet || true)"
[[ -z "$running" ]] || { echo "stop the gateway first: docker compose stop ratchet" >&2; exit 1; }

dir="$(cd "$(dirname "$file")" && pwd)"
docker compose run --rm --no-deps -T -v "$dir:/backups:ro" ratchet python - "/backups/$(basename "$file")" /data/ratchet.db <<'PY'
import gzip, os, shutil, sqlite3, sys, time

src, db = sys.argv[1], sys.argv[2]
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
tmp = os.path.join(os.path.dirname(db), f".restore-{stamp}.db")
with gzip.open(src, "rb") as f, open(tmp, "wb") as out:
    shutil.copyfileobj(f, out)
con = sqlite3.connect(tmp)
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
con.close()
if ok != "ok":
    os.remove(tmp)
    sys.exit(f"integrity_check failed: {ok}")
kept = f"ratchet.db.before-restore-{stamp}"
for suffix in ("", "-wal", "-shm"):
    if os.path.exists(db + suffix):
        os.replace(db + suffix, os.path.join(os.path.dirname(db), kept + suffix))
os.replace(tmp, db)
print("restored=" + os.path.basename(src))
print("kept=" + kept)
PY
