#!/usr/bin/env bash
# One command to host Ratchet on Nebius AI Cloud: plan | up | status | down.
#
#   bash deploy/nebius/nebius.sh plan                      # print every command; changes nothing, calls no Nebius API
#   bash deploy/nebius/nebius.sh up                        # the same (the default is a dry run)
#   RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 bash deploy/nebius/nebius.sh up --yes [--with-student] [--with-training] [--live]
#   bash deploy/nebius/nebius.sh status                    # read-only: the VM, the endpoint, the URLs
#   RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 bash deploy/nebius/nebius.sh down --yes
#
# It orchestrates the pieces that already exist, in this order:
#   1. preflight   nebius CLI present and signed in, jq, ssh, the variables each flag needs, a cost estimate (an estimate).
#   2. the VM      deploy/vm/create_vm.sh: CPU VM with the gateway, the simulation host and Caddy (about $0.06 an hour).
#   3. --with-student   a bucket (unless RATCHET_BUCKET_ID is set) and deploy/student_endpoint.sh start: the vLLM + LoRA
#                  endpoint, the one GPU that bills by the hour. --with-training also submits deploy/train_job.sh.
#   4. wiring      RATCHET_STUDENT_BASE and RATCHET_STUDENT_KEY (and, with --live, RATCHET_MODE=live and the keys) are written
#                  to the VM's .env over ssh through deploy/vm/set_env.sh (the value goes on stdin, never on a command line),
#                  then deploy/vm/update.sh rebuilds, checks the config and restarts.
#   5. smoke       scripts/demo_check.py against the new host, then the URLs and the teardown commands.
# `down` stops the student endpoint first (a stopped endpoint is not billed for compute; its mounted bucket still is), then
# deletes the VM and its boot disk. It never deletes the endpoint or the bucket: it prints those commands.
#
# Safety: a run is a dry run unless it has BOTH --yes and RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1; GPU resources need their
# own flags. A dry run (and `plan`) puts a local stand-in for `nebius` on PATH, so no Nebius API is called, and nothing is
# created, stopped or deleted. Exit codes: 0 ok; 1 failure or a missing variable; 2 usage, or a refusal to spend.
#
# SCRIPTED AND TESTED OFFLINE, READY TO RUN. The pieces were written from the CLI reference and tested offline against a
# fake `nebius`. Lines marked VERIFY ON DAY 1 use commands that no script here has used before.
# Guide: docs/WHERE_IT_RUNS.md, docs/DEPLOY_NEBIUS_VM.md
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$DEPLOY/.." && pwd)"
PY="${RATCHET_PYTHON:-python}"

die() {  # die <code> <message>
  local code="$1"
  shift
  echo "error: $*" >&2
  exit "$code"
}

usage() {
  cat >&2 <<'EOF'
usage: bash deploy/nebius/nebius.sh <plan|up|status|down> [--yes] [--with-student] [--with-training] [--live]
  plan      print every command; changes nothing
  up        create the VM (and, with the flags, the student endpoint and a training job); a dry run without --yes
  status    read-only: VM, endpoint, URLs
  down      stop the endpoint, delete the VM and its boot disk; a dry run without --yes
  --yes                 with RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1, really run
  --with-student        also a bucket and the GPU student endpoint (billed by the hour while it runs)
  --with-training       also submit one training job (needs --with-student's bucket and RATCHET_TRAIN_IMAGE)
  --live                switch the VM's gateway to live mode (needs NEBIUS_API_KEY and RATCHET_API_KEYS)
EOF
  exit 2
}

[[ $# -ge 1 ]] || usage
CMD="$1"
shift
YES=0 WITH_STUDENT=0 WITH_TRAINING=0 LIVE=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --with-student) WITH_STUDENT=1 ;;
    --with-training) WITH_TRAINING=1 ;;
    --live) LIVE=1 ;;
    *) echo "unknown option: $arg" >&2; usage ;;
  esac
done
case "$CMD" in plan | up | status | down) ;; *) usage ;; esac

REQ_DRY="${DRY_RUN:-0}"
[[ "$REQ_DRY" == "0" || "$REQ_DRY" == "1" ]] || die 2 "DRY_RUN must be 0 or 1, not $REQ_DRY"
CONFIRM="${RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY:-0}"

# DRY is 1 unless the run is explicitly confirmed twice.
DRY=1
if [[ "$CMD" == "status" ]]; then
  DRY=0
elif [[ "$CMD" != "plan" && "$REQ_DRY" == "0" && "$YES" == "1" ]]; then
  [[ "$CONFIRM" == "1" ]] || die 2 "--yes also needs RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 (this creates paid resources). Nothing was changed."
  DRY=0
fi
if [[ "$CMD" != "plan" && "$CMD" != "status" && "$DRY" == "1" && "$YES" == "1" && "$REQ_DRY" == "1" ]]; then
  echo "note: DRY_RUN=1 is set, so --yes is ignored." >&2
fi
if [[ "$WITH_TRAINING" == "1" && "$WITH_STUDENT" != "1" && "$CMD" == "up" ]]; then
  die 2 "--with-training needs --with-student (the job and the endpoint share the bucket)"
fi

# --- stand-in CLI for dry runs ---------------------------------------------------------
STANDIN=""
cleanup() { [[ -z "$STANDIN" ]] || rm -rf "$STANDIN"; }
trap cleanup EXIT
if [[ "$DRY" == "1" ]]; then
  STANDIN="$(mktemp -d)"
  printf '#!/usr/bin/env bash\n# stand-in for the nebius CLI in a dry run: prints nothing, calls nothing\nexit 0\n' >"$STANDIN/nebius"
  chmod +x "$STANDIN/nebius"
  export PATH="$STANDIN:$PATH"
fi

# --- settings (the same names create_vm.sh and student_endpoint.sh read) --------------------
VM_NAME="${RATCHET_VM_NAME:-ratchet-demo}"
SSH_USER="${RATCHET_SSH_USER:-ubuntu}"
PLATFORM="${RATCHET_VM_PLATFORM:-cpu-e2}"
SSH_KEY_PATH="${SSH_PUBLIC_KEY_PATH:-$HOME/.ssh/id_ed25519.pub}"
ENDPOINT_NAME="${RATCHET_ENDPOINT_NAME:-ratchet-student}"
TRAIN_VERSION="${RATCHET_TRAIN_VERSION:-student-v1}"
SMOKE_CMD="${RATCHET_SMOKE_CMD:-}"  # one executable path; default: scripts/demo_check.py
PROJECT="${NEBIUS_PROJECT_ID:-${NEBIUS_PARENT_ID:-}}"
MISSING=()

need() {  # need <name> <what for>: refuse a real run when unset; a dry run notes it and goes on with a placeholder
  local name="$1" why="$2"
  if [[ -z "${!name:-}" ]]; then
    if [[ "$DRY" == "1" ]]; then MISSING+=("$name ($why)"); else die 1 "Set $name ($why)."; fi
  fi
}

show() { echo "+ $*" >&2; }
act() {  # run a command that changes something, or just print it in a dry run
  show "$@"
  if [[ "$DRY" == "1" ]]; then return 0; fi
  "$@"
}

# --- cost estimate (an estimate: list prices read Oct 20-22, 2026, USD, before tax) -------------------
print_estimate() {
  echo "Estimated cost (an estimate from list prices read Oct 20-22, 2026; your bill decides):" >&2
  echo "  CPU VM ${PLATFORM} ${RATCHET_VM_PRESET:-2vcpu-8gb} + ${RATCHET_VM_DISK_GB:-30} GiB disk: about \$0.06 to \$0.07 an hour (cpu-e2 \$0.060/h; the disk about \$0.003/h)" >&2
  if [[ "$WITH_STUDENT" == "1" ]]; then
    echo "  student endpoint (L40S, ${RATCHET_SERVE_PRESET:-1gpu-8vcpu-32gb}): about \$1.35 an hour while it runs, about \$32 a day if left on" >&2
    echo "    a stopped endpoint is not billed for compute; the mounted bucket is billed either way" >&2
  fi
  if [[ "$WITH_TRAINING" == "1" ]]; then
    echo "  training job (L40S): about \$1.35 an hour, capped at the job's 2 h timeout: at most about \$2.70 (estimate)" >&2
  fi
  echo "  sources: docs/DEPLOY_NEBIUS_VM.md#cost-estimate, docs/DEPLOYMENT.md#4-cost-estimates" >&2
}

banner() {
  echo "Ratchet on Nebius: $CMD" >&2
  echo "SCRIPTED AND TESTED OFFLINE against a fake CLI: ready to run." >&2
  if [[ "$DRY" == "1" ]]; then
    echo "DRY RUN: nothing will be created, started, stopped or deleted, and no Nebius API is called." >&2
    if [[ "$CMD" == "up" || "$CMD" == "down" ]]; then
      echo "  To run for real, repeat the command with --yes and RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 in front." >&2
    fi
  else
    echo "REAL RUN: this creates or deletes paid Nebius resources." >&2
  fi
}

# --- preflight --------------------------------------------------------------------------------------
region_check() {
  # cpu-e2 exists only in eu-north1 (docs/DEPLOY_NEBIUS_VM.md). NEBIUS_REGION is optional: set it to get the check.
  local region="${NEBIUS_REGION:-}"
  if [[ -n "$region" && "$PLATFORM" == "cpu-e2" && "$region" != "eu-north1" ]]; then
    die 1 "RATCHET_VM_PLATFORM=cpu-e2 exists only in eu-north1, not $region: set RATCHET_VM_PLATFORM=cpu-d3"
  fi
}

preflight_up() {
  need NEBIUS_PROJECT_ID "your Nebius project ID, used as --parent-id"
  if [[ "$WITH_STUDENT" == "1" ]]; then need RATCHET_STUDENT_KEY "the token clients send to the endpoint, e.g. openssl rand -hex 24"; fi
  if [[ "$WITH_TRAINING" == "1" ]]; then need RATCHET_TRAIN_IMAGE "the training image built from deploy/Dockerfile.train and pushed to a registry Nebius can pull"; fi
  if [[ "$LIVE" == "1" ]]; then
    need NEBIUS_API_KEY "the Token Factory key (teacher)"
    need RATCHET_API_KEYS "gateway API keys, name:key,... (live mode refuses to start without them)"
  fi
  region_check
  if [[ "$DRY" == "1" ]]; then
    PROJECT="${PROJECT:-<project-id>}"
    return 0
  fi
  [[ -r "$SSH_KEY_PATH" ]] || die 1 "SSH public key not found at $SSH_KEY_PATH (set SSH_PUBLIC_KEY_PATH)"
  for tool in nebius jq ssh; do
    command -v "$tool" >/dev/null || die 1 "$tool not found (nebius: curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash)"
  done
  command -v "$PY" >/dev/null || die 1 "$PY not found: set RATCHET_PYTHON to a Python 3"
  # Signed in? VERIFY ON DAY 1: `nebius iam whoami` is the call this check relies on.
  show nebius iam whoami --format json
  nebius iam whoami --format json >/dev/null || die 1 "the nebius CLI is not signed in (nebius profile create --parent-id $PROJECT)"
  # The project has a subnet (create_vm.sh picks the first one unless NEBIUS_SUBNET_ID is set).
  if [[ -z "${NEBIUS_SUBNET_ID:-}" ]]; then
    show nebius vpc subnet list --parent-id "$PROJECT" --format jsonpath='{.items[0].metadata.id}'
    local subnet
    subnet="$(nebius vpc subnet list --parent-id "$PROJECT" --format jsonpath='{.items[0].metadata.id}')" || subnet=""
    [[ -n "$subnet" ]] || die 1 "no subnet in project $PROJECT; set NEBIUS_SUBNET_ID (nebius vpc subnet list)"
  fi
}

ssh_target() { echo "${SSH_USER}@${VM_IP}"; }
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15)
if [[ -f "${SSH_KEY_PATH%.pub}" ]]; then SSH_OPTS+=(-i "${SSH_KEY_PATH%.pub}"); fi

remote() {  # remote <command>: one command on the VM
  act ssh "${SSH_OPTS[@]}" "$(ssh_target)" "$1"
}

remote_wait_boot() {
  show ssh "${SSH_OPTS[@]}" "$(ssh_target)" "cloud-init status --wait"
  if [[ "$DRY" == "1" ]]; then return 0; fi
  local i
  for i in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "$(ssh_target)" "cloud-init status --wait" >/dev/null 2>&1; then return 0; fi
    echo "  waiting for the VM to accept ssh and finish first boot ($i/30)" >&2
    sleep 10
  done
  die 1 "the VM did not finish first boot in 5 minutes: ssh $(ssh_target) 'sudo tail -f /var/log/ratchet-bootstrap.log'"
}

set_remote_env() {  # set_remote_env <NAME> <value>: the value travels on stdin, never in a command line or the echo
  local name="$1" value="$2"
  show "printf %s <hidden> | ssh ... $(ssh_target) sudo -u ratchet /opt/ratchet/deploy/vm/set_env.sh $name"
  if [[ "$DRY" == "1" ]]; then return 0; fi
  printf '%s' "$value" | ssh "${SSH_OPTS[@]}" "$(ssh_target)" "sudo -u ratchet /opt/ratchet/deploy/vm/set_env.sh $name"
}

# --- up ---------------------------------------------------------------------------------------------
VM_ID="" DISK_ID="" VM_IP="" DOMAIN="" BUCKET_ID="${RATCHET_BUCKET_ID:-}" STUDENT_BASE="" ENDPOINT_ID=""

step_vm() {
  echo "== 1. CPU VM: the gateway, the simulation host and Caddy" >&2
  local key="$SSH_KEY_PATH" out
  if [[ "$DRY" == "1" ]]; then  # a dry run never reads or prints your key
    key="$STANDIN/placeholder_key.pub"
    echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPlanPlaceholderKeyNotUsed plan@example" >"$key"
  fi
  out="$(NEBIUS_PROJECT_ID="$PROJECT" SSH_PUBLIC_KEY_PATH="$key" DRY_RUN="$DRY" bash "$DEPLOY/vm/create_vm.sh")" \
    || die 1 "deploy/vm/create_vm.sh failed (see above); nothing after it ran"
  printf '%s\n' "$out"
  VM_ID="$(printf '%s\n' "$out" | sed -n 's/^  VM ID: *//p' | head -n1)"
  DISK_ID="$(printf '%s\n' "$out" | sed -n 's/^  Boot disk: *//p' | head -n1)"
  VM_IP="$(printf '%s\n' "$out" | sed -n 's/^  Public IP: *//p' | head -n1)"
  DOMAIN="$(printf '%s\n' "$out" | sed -n 's/^  Domain: *//p' | head -n1)"
  [[ -n "$VM_ID" && -n "$VM_IP" && -n "$DOMAIN" ]] || die 1 "could not read the VM id, IP and domain from create_vm.sh's summary"
}

step_student() {
  echo "== 2. Student endpoint (the GPU part; billed by the hour while it runs)" >&2
  if [[ -z "$BUCKET_ID" ]]; then
    local bucket="${VM_NAME}-data"
    # VERIFY ON DAY 1: `nebius storage bucket create --help` (flags and the id's jsonpath).
    show nebius storage bucket create --parent-id "$PROJECT" --name "$bucket" --format jsonpath='{.metadata.id}'
    if [[ "$DRY" == "1" ]]; then BUCKET_ID="<bucket-id>"; else
      BUCKET_ID="$(nebius storage bucket create --parent-id "$PROJECT" --name "$bucket" --format jsonpath='{.metadata.id}')" \
        || die 1 "bucket creation failed"
      [[ -n "$BUCKET_ID" ]] || die 1 "bucket creation returned no id"
    fi
  fi
  local key="${RATCHET_STUDENT_KEY:-<student-key>}"
  RATCHET_BUCKET_ID="$BUCKET_ID" RATCHET_STUDENT_KEY="$key" DRY_RUN="$DRY" bash "$DEPLOY/student_endpoint.sh" start \
    || die 1 "deploy/student_endpoint.sh start failed (see above)"
  echo "  The endpoint bills by the hour until it is stopped:  bash deploy/student_endpoint.sh stop" >&2
  if [[ "$WITH_TRAINING" == "1" ]]; then
    echo "== 2b. Training job (needs the dataset at the bucket's night/train.jsonl)" >&2
    RATCHET_BUCKET_ID="$BUCKET_ID" RATCHET_TRAIN_IMAGE="${RATCHET_TRAIN_IMAGE:-<train-image>}" DRY_RUN="$DRY" \
      bash "$DEPLOY/train_job.sh" "$TRAIN_VERSION" || die 1 "deploy/train_job.sh failed (see above)"
  fi
  # The endpoint's address: VERIFY ON DAY 1 (`nebius ai endpoint get --id <id>`, status.public_endpoints).
  if [[ "$DRY" == "1" ]]; then
    ENDPOINT_ID="<endpoint-id>"
    STUDENT_BASE="https://<endpoint-address>/v1"
    show nebius ai endpoint get --id "$ENDPOINT_ID" --format jsonpath='{.status.public_endpoints[0]}'
    return 0
  fi
  local status host
  status="$(RATCHET_BUCKET_ID="$BUCKET_ID" bash "$DEPLOY/student_endpoint.sh" status)" || die 1 "could not read the endpoint's id"
  ENDPOINT_ID="${status%% *}"
  show nebius ai endpoint get --id "$ENDPOINT_ID" --format jsonpath='{.status.public_endpoints[0]}'
  host="$(nebius ai endpoint get --id "$ENDPOINT_ID" --format jsonpath='{.status.public_endpoints[0]}')" || host=""
  [[ -n "$host" ]] || die 1 "the endpoint $ENDPOINT_ID has no public address yet; set RATCHET_STUDENT_BASE on the VM by hand once it has"
  host="${host#https://}"
  STUDENT_BASE="https://${host%/}/v1"
  echo "  Endpoint ${ENDPOINT_ID}: ${status#* } (the first start can take several minutes; /readyz says when the student answers)" >&2
}

step_wire() {
  echo "== 3. Wire the VM's .env" >&2
  remote_wait_boot
  if [[ "$WITH_STUDENT" == "1" ]]; then
    set_remote_env RATCHET_STUDENT_BASE "$STUDENT_BASE"
    set_remote_env RATCHET_STUDENT_KEY "${RATCHET_STUDENT_KEY:-<student-key>}"
  fi
  if [[ "$LIVE" == "1" ]]; then
    set_remote_env NEBIUS_API_KEY "${NEBIUS_API_KEY:-<nebius-api-key>}"
    set_remote_env RATCHET_API_KEYS "${RATCHET_API_KEYS:-<api-keys>}"
    if [[ -n "${TAVILY_API_KEY:-}" ]]; then set_remote_env TAVILY_API_KEY "$TAVILY_API_KEY"; fi
    set_remote_env RATCHET_MODE live
  fi
  if [[ "$WITH_STUDENT" == "1" || "$LIVE" == "1" ]]; then
    # update.sh rebuilds, runs `python -m ratchet.config check` (exit 2 stops it before the running gateway changes) and restarts.
    remote "/opt/ratchet/deploy/vm/update.sh --skip-backup --no-probe"
  else
    echo "  Nothing to wire: no --with-student and no --live." >&2
  fi
}

step_smoke() {
  echo "== 4. Smoke check from outside" >&2
  local mode=mock
  [[ "$LIVE" == "1" ]] && mode=live
  local url="https://${DOMAIN}" cmd i
  if [[ -n "$SMOKE_CMD" ]]; then cmd=("$SMOKE_CMD" "$url" --mode "$mode"); else cmd=("$PY" "$ROOT/scripts/demo_check.py" "$url" --mode "$mode"); fi
  show "${cmd[@]}"
  if [[ "$DRY" == "1" ]]; then return 0; fi
  for i in 1 2 3 4 5 6; do
    if "${cmd[@]}"; then return 0; fi
    echo "  not healthy yet (certificate or first boot); retrying in 30 s ($i/6)" >&2
    sleep 30
  done
  echo "warning: the smoke check did not pass; the resources are still running. Look: ssh $(ssh_target) 'sudo tail -n 50 /var/log/ratchet-bootstrap.log'" >&2
  SMOKE_FAILED=1
}

SMOKE_FAILED=0

print_done() {
  echo >&2
  echo "== Done" >&2
  if [[ "$DRY" == "1" ]]; then
    echo "This was a dry run. Nothing exists." >&2
  else
    echo "Gateway:     https://${DOMAIN}/   (dashboard /dashboard, Scout /scout)" >&2
    echo "Simulation:  https://sim.${DOMAIN}/" >&2
    [[ "$WITH_STUDENT" == "1" ]] && echo "Student:     ${STUDENT_BASE} (token in RATCHET_STUDENT_KEY)" >&2
  fi
  if [[ "$WITH_STUDENT" == "1" ]]; then
    echo "STOP THE GPU WHEN IDLE:   bash deploy/student_endpoint.sh stop" >&2
  fi
  echo "Status:      bash deploy/nebius/nebius.sh status" >&2
  echo "Teardown:    RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 bash deploy/nebius/nebius.sh down --yes" >&2
  if [[ "${#MISSING[@]}" -gt 0 ]]; then
    echo "Needed before a real run (unset now):" >&2
    printf '  %s\n' "${MISSING[@]}" >&2
  fi
}

cmd_up() {
  banner
  print_estimate
  preflight_up
  step_vm
  if [[ "$WITH_STUDENT" == "1" ]]; then step_student; fi
  step_wire
  step_smoke
  print_done
  [[ "$SMOKE_FAILED" == "0" ]] || exit 1
}

# --- status -----------------------------------------------------------------------------------------
cmd_status() {
  banner
  [[ -n "$PROJECT" ]] || die 1 "Set NEBIUS_PROJECT_ID."
  command -v nebius >/dev/null || die 1 "nebius CLI not found"
  local state ip id
  show nebius compute instance get-by-name --parent-id "$PROJECT" --name "$VM_NAME" --format json
  if id="$(nebius compute instance get-by-name --parent-id "$PROJECT" --name "$VM_NAME" --format jsonpath='{.metadata.id}' 2>/dev/null)" && [[ -n "$id" ]]; then
    state="$(nebius compute instance get --id "$id" --format jsonpath='{.status.state}' 2>/dev/null || true)"
    ip="$(nebius compute instance get --id "$id" --format jsonpath='{.status.network_interfaces[0].public_ip_address.address}' 2>/dev/null || true)"
    ip="${ip%%/*}"
    echo "VM:        $VM_NAME ($id) ${state:-unknown}" >&2
    [[ -z "$ip" ]] || echo "Address:   https://${RATCHET_DOMAIN:-${ip}.sslip.io}/   simulation https://sim.${RATCHET_DOMAIN:-${ip}.sslip.io}/" >&2
  else
    echo "VM:        none named $VM_NAME" >&2
  fi
  local ep
  if ep="$(RATCHET_BUCKET_ID="${RATCHET_BUCKET_ID:-x}" DRY_RUN=0 bash "$DEPLOY/student_endpoint.sh" status 2>/dev/null)"; then
    echo "Endpoint:  $ENDPOINT_NAME $ep   (stop it when idle: bash deploy/student_endpoint.sh stop)" >&2
  else
    echo "Endpoint:  none named $ENDPOINT_NAME" >&2
  fi
}

# --- down -------------------------------------------------------------------------------------------
cmd_down() {
  banner
  need NEBIUS_PROJECT_ID "your Nebius project ID"
  PROJECT="${PROJECT:-<project-id>}"
  echo "== 1. Stop the student endpoint first (a stopped endpoint is not billed for compute; its bucket still is)" >&2
  if [[ "$DRY" == "1" ]]; then
    RATCHET_BUCKET_ID="${RATCHET_BUCKET_ID:-<bucket-id>}" DRY_RUN=1 bash "$DEPLOY/student_endpoint.sh" stop 2>&1 \
      | sed 's/^/  /' >&2 || true
    echo "  (a dry run cannot look the endpoint up: down skips this step when none exists)" >&2
  else
    if RATCHET_BUCKET_ID="${RATCHET_BUCKET_ID:-x}" DRY_RUN=0 bash "$DEPLOY/student_endpoint.sh" status >/dev/null 2>&1; then
      RATCHET_BUCKET_ID="${RATCHET_BUCKET_ID:-x}" DRY_RUN=0 bash "$DEPLOY/student_endpoint.sh" stop \
        || die 1 "could not stop the endpoint: nothing was deleted. Fix that first, or stop it in the console."
    else
      echo "  no endpoint named $ENDPOINT_NAME: nothing to stop" >&2
    fi
  fi
  echo "== 2. Delete the VM, then its boot disk" >&2
  local vm="<vm-id>" disk="<disk-id>"
  if [[ "$DRY" == "0" ]]; then
    command -v nebius >/dev/null || die 1 "nebius CLI not found"
    show nebius compute instance get-by-name --parent-id "$PROJECT" --name "$VM_NAME" --format jsonpath='{.metadata.id}'
    vm="$(nebius compute instance get-by-name --parent-id "$PROJECT" --name "$VM_NAME" --format jsonpath='{.metadata.id}' 2>/dev/null)" || vm=""
    show nebius compute disk get-by-name --parent-id "$PROJECT" --name "${VM_NAME}-boot" --format jsonpath='{.metadata.id}'
    disk="$(nebius compute disk get-by-name --parent-id "$PROJECT" --name "${VM_NAME}-boot" --format jsonpath='{.metadata.id}' 2>/dev/null)" || disk=""
  fi
  if [[ -n "$vm" ]]; then act nebius compute instance delete --id "$vm"; else echo "  no VM named $VM_NAME" >&2; fi
  if [[ -n "$disk" ]]; then act nebius compute disk delete --id "$disk"; else echo "  no disk named ${VM_NAME}-boot" >&2; fi
  echo >&2
  echo "Not deleted (still billed if they exist): the student endpoint and the bucket. To remove them:" >&2
  echo "  nebius ai endpoint delete --id <endpoint-id>      (id: bash deploy/student_endpoint.sh status)   VERIFY ON DAY 1" >&2
  echo "  nebius storage bucket delete --id <bucket-id>     (empty it first; copy the adapters out if you want them)   VERIFY ON DAY 1" >&2
  echo "Also remove the DNS record (if you made one) and rotate the keys." >&2
}

case "$CMD" in
  plan) cmd_up ;;
  up) cmd_up ;;
  status) cmd_status ;;
  down) cmd_down ;;
esac
