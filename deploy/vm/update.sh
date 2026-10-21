#!/usr/bin/env bash
# Update the demo on the VM: back up, pull, rebuild, check the config, restart, verify, and roll back if the new version fails.
#   sudo -u ratchet /opt/ratchet/deploy/vm/update.sh     (or run it as your admin user; it switches)
#   flags: --skip-backup  --no-rollback  --no-probe        DRY_RUN=1 prints every command that changes something
#
# Before anything changes it saves the running git SHA, makes a ledger backup (backup.sh, live mode) and tags the running image
# ratchet-gateway:previous (a tagged image survives `docker image prune -f`). When the new version is unhealthy, not ready, in
# the wrong mode or fails the live probe it moves the checkout and the image back. Token Factory not answering (the probe's
# teacher side) never rolls back: an older image would not fix it. The ledger backup is restored only when the old image also
# cannot open the ledger (ADR 0132). It builds, checks and verifies both gateways (live, and the simulation host,
# ADR 0133); a simulation container that is unhealthy, not ready or not in mock mode is rolled back like a bad live one. Migrations only add columns and tables, so the previous image reads a migrated ledger.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

# Run as the user that owns the checkout, so git and Docker never leave root-owned files behind.
owner="$(stat -c %U "$REPO")"
if [[ "$(id -un)" != "$owner" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then exec runuser -u "$owner" -- "$(readlink -f "$0")" "$@"; fi
  exec sudo -u "$owner" -- "$(readlink -f "$0")" "$@"
fi

env_get() { grep -E "^$1=" "$HERE/.env" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
die() { echo "$*" >&2; exit 1; }
DRY="${DRY_RUN:-0}"
# A command that changes something: printed instead of run in a dry run.
act() { if [[ "$DRY" == "1" ]]; then echo "+ $*"; else "$@"; fi; }
pause() { [[ "$DRY" == "1" ]] || sleep "$1"; }

# All of it is inside main, so bash has read the whole script before `git pull` replaces it on disk.
main() {
  local skip_backup=0 rollback=1 probe=1
  for arg in "$@"; do
    case "$arg" in
      --skip-backup) skip_backup=1 ;;
      --no-rollback) rollback=0 ;;
      --no-probe) probe=0 ;;
      *) die "unknown option: $arg" ;;
    esac
  done
  [[ -f "$HERE/.env" ]] || die "missing $HERE/.env (cp .env.vm.example .env and edit it)"
  local state mode domain
  state="$(env_get RATCHET_WATCH_STATE_DIR)"; state="${state:-/var/lib/ratchet}"
  mode="$(env_get RATCHET_MODE)"; mode="${mode:-mock}"
  domain="$(env_get RATCHET_DOMAIN)"
  # The two hosts (FR-11.30): the live gateway at RATCHET_DOMAIN and, with RATCHET_SIM=on (the default), the simulation (mock
  # mode, no keys) at RATCHET_SIM_DOMAIN. Each problem says what to fix and stops before anything is built or replaced.
  local sim sim_domain sim_key problems=0
  sim="$(env_get RATCHET_SIM)"; sim="${sim:-on}"
  sim_domain="$(env_get RATCHET_SIM_DOMAIN)"
  [[ -n "$sim_domain" || -z "$domain" ]] || sim_domain="sim.${domain}"
  if [[ "$sim" != "on" && "$sim" != "off" ]]; then
    echo "RATCHET_SIM must be on or off, not '$sim': fix $HERE/.env" >&2; problems=1
  fi
  if [[ "$sim" == "on" && -n "$domain" && "$sim_domain" == "$domain" ]]; then
    echo "RATCHET_SIM_DOMAIN equals RATCHET_DOMAIN: Caddy would refuse two sites with one name; give the simulation its own (default sim.<domain>)" >&2; problems=1
  fi
  sim_key="$(env_get RATCHET_SIM_BOM_SIGNING_KEY)"
  if [[ -n "$sim_key" && "$sim_key" == "$(env_get RATCHET_BOM_SIGNING_KEY)" ]]; then
    echo "RATCHET_SIM_BOM_SIGNING_KEY equals RATCHET_BOM_SIGNING_KEY: the simulation must never sign with the live key" >&2; problems=1
  fi
  [[ "$problems" == "0" ]] || exit 1
  if [[ "$sim" == "off" ]] && ! grep -qE '^RATCHET_SIM_URL=' "$HERE/.env"; then
    echo "warning: RATCHET_SIM=off but .env has no RATCHET_SIM_URL= line: the live pages would link a host that is not served; add an empty RATCHET_SIM_URL=" >&2
  fi

  # 1. One update at a time; the watchdog never restarts the gateway while this lock is held.
  mkdir -p "$state" 2>/dev/null || true
  if [[ "$DRY" != "1" ]]; then
    exec 9>"$state/update.lock"
    flock -n 9 || die "another update is running"
  fi

  # 2. What to go back to.
  local prev_sha prev_img
  prev_sha="$(git -C "$REPO" rev-parse HEAD)"
  prev_img="$(docker image inspect -f '{{.Id}}' ratchet-gateway:local 2>/dev/null || true)"

  # 3. Backup first (live mode; backup.sh exits 0 in mock mode and prints no backup_file line).
  local backup_file=""
  if [[ "$skip_backup" == "1" ]]; then
    echo "updating without a backup: a failed migration cannot be undone" >&2
  elif [[ "$DRY" == "1" ]]; then
    echo "+ RATCHET_BACKUP_TAG=pre-update $HERE/backup.sh"
  else
    echo "== backup"
    out="$(RATCHET_BACKUP_TAG=pre-update "$HERE/backup.sh")" || die "backup failed: nothing was changed (use --skip-backup to update without one)"
    echo "$out"
    backup_file="$(grep '^backup_file=' <<<"$out" | tail -n1 | cut -d= -f2- || true)"
  fi

  # 4. Tag the running image.
  if [[ -n "$prev_img" || "$DRY" == "1" ]]; then act docker tag ratchet-gateway:local ratchet-gateway:previous; fi

  echo "== git pull"
  act git -C "$REPO" pull --ff-only
  local new_sha
  new_sha="$(git -C "$REPO" rev-parse HEAD)"

  # Settings the example lists and .env does not set (names only): the gateway's own default applies to each, which may not be
  # the example's. Not an error: copy the ones you want from .env.vm.example.
  local missing
  missing="$(bash "$HERE/env_missing.sh" "$HERE/.env.vm.example" "$HERE/.env" | paste -sd ' ' -)"
  if [[ -n "${missing// /}" ]]; then
    echo "note: .env does not set these settings from .env.vm.example (the defaults apply): ${missing% }" >&2
  fi

  echo "== docker compose build"
  cd "$HERE"
  act docker compose pull caddy
  act docker compose build ratchet ratchet-sim  # one image: the second build is a cache hit
  # restore.sh from the new checkout, kept outside it so a rollback to an older checkout still has one.
  local work
  work="$(mktemp -d)"
  cp "$HERE/restore.sh" "$work/restore.sh" 2>/dev/null || true

  # The gateway's startup check, in the new image with this .env, before the running container is replaced (ADR 0070):
  # exit 2 means the new gateway would refuse to start. Put the old image and checkout back, so no later `up` starts the
  # refused image, and stop. Exit 1 (warnings) goes on.
  echo "== python -m ratchet.config check (new image, deploy/vm/.env)"
  local check=0
  act docker compose run --rm --no-deps -T ratchet python -m ratchet.config check || check=$?
  if [[ "$check" -ge 2 ]]; then
    [[ -z "$prev_img" ]] || docker tag ratchet-gateway:previous ratchet-gateway:local
    git -C "$REPO" reset --hard "$prev_sha" >/dev/null
    die "configuration errors (above): fix $HERE/.env and run update.sh again; the running gateway was left as it is"
  fi
  # The simulation has its own environment: a bad RATCHET_DEMO_* value stops the update here too.
  echo "== python -m ratchet.config check (ratchet-sim)"
  check=0
  act docker compose run --rm --no-deps -T ratchet-sim python -m ratchet.config check || check=$?
  if [[ "$check" -ge 2 ]]; then
    [[ -z "$prev_img" ]] || docker tag ratchet-gateway:previous ratchet-gateway:local
    git -C "$REPO" reset --hard "$prev_sha" >/dev/null
    die "configuration errors in the simulation's environment (above): fix deploy/vm/docker-compose.yml; the running gateways were left as they are"
  fi

  echo "== docker compose up -d"
  act docker compose up -d --remove-orphans
  local result
  result="$(verify "$mode" "$domain" "$probe")"
  echo "verify: $result"

  local code=0
  case "$result" in
    ok) ;;
    teacher_only)
      echo "Token Factory is not answering, which an older image would not fix: see RUNBOOK 'Teacher API down'" >&2
      code=1 ;;
    fail:*)
      code=1
      if [[ "$rollback" == "0" ]]; then
        echo "update to ${new_sha:0:7} failed (${result#fail:}); --no-rollback: left as it is" >&2
      else
        echo "update to ${new_sha:0:7} failed (${result#fail:}); rolling back to ${prev_sha:0:7}" >&2
        act git -C "$REPO" reset --hard "$prev_sha"
        [[ -z "$prev_img" ]] || act docker tag ratchet-gateway:previous ratchet-gateway:local
        act docker compose up -d --no-build --force-recreate --remove-orphans
        local again
        again="$(verify "$mode" "$domain" 0)"
        echo "after the rollback: $again"
        notify watchdog_restart update rolled_back "${new_sha:0:7}"
        # The ledger comes back from the backup only when the old image cannot open it either.
        if [[ "$mode" == "live" && -n "$backup_file" && ( "$again" == "fail:unhealthy" || "$again" == "fail:ledger" ) ]]; then
          act docker compose stop ratchet
          act bash "$work/restore.sh" "$backup_file"
          act docker compose start ratchet
          echo "restored the ledger from $backup_file; requests logged after it are lost; the replaced file is kept as /data/ratchet.db.before-restore-*" >&2
          notify watchdog_restart update ledger_restored ""
        fi
      fi ;;
  esac

  check_pages "$domain" "$sim" "$sim_domain" || code=1
  act docker image prune -f
  rm -rf "$work"
  [[ "$code" == "0" ]] || die "update finished with problems: see docker compose logs"
  echo "== ok"
}

# Best-effort alert through the watchdog's webhook; never fails the update.
notify() {
  [[ "$DRY" == "1" ]] && { echo "+ python3 -m ratchet.watch notify --kind $1 --check $2 --code $3 ${4:+--sha $4}"; return 0; }
  (cd "$REPO" && python3 -m ratchet.watch notify --kind "$1" --check "$2" --code "$3" ${4:+--sha "$4"}) >/dev/null 2>&1 || true
}

# verify MODE DOMAIN PROBE: one line, `ok`, `teacher_only` or `fail:<reason>`. Public page failures are reported, never a
# reason to roll back by themselves.
verify() {
  local mode="$1" domain="$2" probe="$3"
  if [[ "$DRY" == "1" ]]; then echo ok; return 0; fi
  local cid status="unknown"
  cid="$(docker compose ps -q ratchet)"
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
    [[ "$status" == "healthy" ]] && break
    sleep 5
  done
  [[ "$status" == "healthy" ]] || { echo "fail:unhealthy"; return 0; }
  # Liveness says the process answers; readiness says it can serve: 200 is ready or degraded, 503 not. A 503 whose ledger
  # check is fine is the teacher: nothing to roll back. A 404 (an image without /readyz) falls back to /healthz.
  local snippet='
import json, sys, urllib.error, urllib.request
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/readyz", timeout=5)
    code, body = r.status, r.read().decode()
except urllib.error.HTTPError as e:
    code, body = e.code, e.read().decode()
except Exception as e:
    code, body = 0, ""
ledger = True
try:
    ledger = json.loads(body)["checks"]["ledger"].get("ok") is not False
except Exception:
    pass
print(code, "ledger_ok" if ledger else "ledger_bad")
'
  local ready="" r
  for _ in $(seq 1 24); do
    r="$(docker compose exec -T ratchet python -c "$snippet" 2>/dev/null || true)"
    ready="$r"
    [[ "$r" == 200* || "$r" == "404"* ]] && break
    [[ "$r" == 503*ledger_bad ]] && break
    sleep 5
  done
  [[ "$ready" == *ledger_bad ]] && { echo "fail:ledger"; return 0; }
  local teacher_only=0
  [[ "$ready" == 503* ]] && teacher_only=1
  local reported
  reported="$(docker compose exec -T ratchet python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=5)).get('mode',''))" 2>/dev/null || true)"
  [[ "$reported" == "$mode" ]] || { echo "fail:mode"; return 0; }
  # The simulation container (same image, mock mode, no keys): it must be healthy, ready and in mock mode, or the new image
  # is bad for it and the update is rolled back like a bad live gateway. Its readiness checks the ledger only.
  local scid sstatus="unknown" sready=0
  scid="$(docker compose ps -q ratchet-sim)"
  for _ in $(seq 1 60); do
    sstatus="$(docker inspect -f '{{.State.Health.Status}}' "$scid" 2>/dev/null || echo unknown)"
    [[ "$sstatus" == "healthy" ]] && break
    sleep 5
  done
  echo "ratchet-sim container health: $sstatus" >&2
  [[ "$sstatus" == "healthy" ]] || { echo "fail:sim_unhealthy"; return 0; }
  for _ in $(seq 1 24); do
    r="$(docker compose exec -T ratchet-sim python -c "$snippet" 2>/dev/null || true)"
    if [[ "$r" == 200* ]]; then sready=1; break; fi
    sleep 5
  done
  echo "ratchet-sim readiness: $([[ "$sready" == "1" ]] && echo ready || echo "not ready (see docker compose logs ratchet-sim)")" >&2
  [[ "$sready" == "1" ]] || { echo "fail:sim_not_ready"; return 0; }
  reported="$(docker compose exec -T ratchet-sim python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=5)).get('mode',''))" 2>/dev/null || true)"
  [[ "$reported" == "mock" ]] || { echo "fail:sim_mode"; return 0; }
  if [[ "$mode" == "live" && "$probe" == "1" && -n "$domain" ]]; then
    local pc=0
    (cd "$REPO" && python3 -m ratchet.watch probe --once --no-flag --no-alert >/dev/null 2>&1) || pc=$?
    case "$pc" in
      3) echo "fail:probe"; return 0 ;;
      4) teacher_only=1 ;;
      5) echo "warning: the live probe could not be run as configured (exit 5): see 'python3 -m ratchet.watch probe --once'" >&2 ;;
    esac
  fi
  [[ "$teacher_only" == "1" ]] && echo teacher_only || echo ok
}

# One host's public paths, each of which must answer 200. With EXPECT, /healthz must also report that mode: a simulation host
# answering in live mode is a misconfiguration (it would hold keys), and this catches it.
check_host() {
  local base="$1" expect="$2" path code body fail=0; shift 2
  for path in "$@"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${base}${path}" || echo 000)"
    echo "${base}${path} -> ${code}"
    [[ "$code" == "200" ]] || fail=1
  done
  if [[ -n "$expect" ]]; then
    body="$(curl -s --max-time 15 "${base}/healthz" || true)"
    if [[ "$body" != *"\"mode\":\"${expect}\""* && "$body" != *"\"mode\": \"${expect}\""* ]]; then
      echo "the simulation host is not in ${expect} mode (${base}/healthz): its container must be mock, with no keys" >&2
      fail=1
    fi
  fi
  return "$fail"
}

# The public pages of both hosts over HTTPS: a page that does not answer 200 makes the update "finished with problems", never
# a rollback. Public routes only: /v1/* may be blocked at the edge, and the dashboard API needs a key when it is private.
check_pages() {
  local domain="$1" sim="$2" sim_domain="$3" fail=0
  [[ -n "$domain" && "$DRY" != "1" ]] || return 0
  local paths=(/ /healthz /readyz /dashboard /scout)
  local public
  public="$(env_get RATCHET_PUBLIC_DASHBOARD)"
  if [[ "$public" == "1" || ( -z "$public" && "$(env_get RATCHET_MODE)" != "live" ) ]]; then paths+=(/ratchet/stats); fi
  check_host "https://${domain}" "" "${paths[@]}" || fail=1
  if [[ "$sim" == "on" ]]; then
    check_host "https://${sim_domain}" mock / /healthz /readyz /dashboard /scout /ratchet/stats || fail=1
  fi
  return "$fail"
}

main "$@"
