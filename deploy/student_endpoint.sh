#!/usr/bin/env bash
# Serve the student on a Nebius Serverless Endpoint with vLLM + LoRA hot-loading.
#   bash deploy/student_endpoint.sh create   # create the endpoint, once
#   bash deploy/student_endpoint.sh start    # start it again (the default; creates it when none has the name)
#   bash deploy/student_endpoint.sh stop     # stop it when idle
#   bash deploy/student_endpoint.sh status   # print its id and state
#   DRY_RUN=1 bash deploy/student_endpoint.sh <action>
#     create: nebius validates the request (--dry-run) and creates nothing;
#     start, stop: the command is printed, not run (the CLI has no --dry-run for them).
#     The read-only lookup (`nebius ai endpoint list`) still runs.
#
# A stopped endpoint is not billed for compute or its own storage; the mounted bucket still is
# (docs.nebius.com/serverless/overview, read Oct 21, 2026). start and stop reuse the same endpoint:
# `nebius ai endpoint start|stop` take its id, found by name (RATCHET_ENDPOINT_NAME) in
# `nebius ai endpoint list --format json`, or given as RATCHET_ENDPOINT_ID (no lookup).
# New adapters are loaded at runtime by ratchet.live (POST /v1/load_lora_adapter), so promotions need
# no restart; a restart forgets them and the gateway loads them again.
# create, start and stop also record the endpoint's uptime in the ledger for the cost report
# (`python -m ratchet.cost endpoint up|down`, FR-15.2). That needs the ledger file (RATCHET_DB)
# on this machine; it is best effort and never fails the deploy. Otherwise run the same command
# where the ledger is, or POST /ratchet/cost/endpoint with an admin key.
# Flags follow the Nebius CLI reference (auto generated on 19-Oct-2026, read Oct 21, 2026) and the
# model card's vLLM flags (huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16, read Oct 21, 2026).
# `nebius ai endpoint get-by-name` would need --parent-id and does not document a missing name, so the
# lookup reads the list instead.
# VERIFY ON DAY 1 with `nebius ai endpoint create --help`, `... start --help` and `... stop --help`,
# and paste the JSON `nebius ai endpoint list --format json` prints (ids redacted) into
# tests/fixtures/nebius_endpoint_list.json.
set -euo pipefail

ACTION="${1:-start}"
NAME="${RATCHET_ENDPOINT_NAME:-ratchet-student}"
BASE="${RATCHET_STUDENT_BASE_MODEL:-nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16}"
PLATFORM="${RATCHET_SERVE_PLATFORM:-gpu-l40s-a}"
PRESET="${RATCHET_SERVE_PRESET:-1gpu-8vcpu-32gb}"
IMAGE="${RATCHET_VLLM_IMAGE:-vllm/vllm-openai:v0.18.0-cu130}"
MAX_LEN="${RATCHET_SERVE_MAX_LEN:-8192}"
MAX_SEQS="${RATCHET_SERVE_MAX_SEQS:-64}"
DEFAULT_TEMPLATE_KWARGS='{"enable_thinking":false}'
TEMPLATE_KWARGS="${RATCHET_SERVE_TEMPLATE_KWARGS-$DEFAULT_TEMPLATE_KWARGS}"  # `-`, not `:-`: set but empty drops the flag
PARSER_PLUGIN="${RATCHET_REASONING_PARSER_PLUGIN:-}"
PY="${RATCHET_PYTHON:-python}"
DRY_RUN="${DRY_RUN:-0}"
ENDPOINT_ID=""
ENDPOINT_STATE=""

die() {  # die <exit code> <message>
  local code="$1"
  shift
  echo "error: $*" >&2
  exit "${code}"
}

case "${ACTION}" in
  create | start | stop | status) ;;
  *)
    echo "usage: bash deploy/student_endpoint.sh [create|start|stop|status]" >&2
    exit 2
    ;;
esac
# Anything but 0 or 1 is refused: DRY_RUN=true must not start a paid GPU because it is not "1".
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || die 2 "DRY_RUN must be 0 or 1, not ${DRY_RUN}"

show() {  # print a command before it runs, never the endpoint's token
  local shown=() hide=0 a
  for a in "$@"; do
    if [[ "${hide}" == 1 ]]; then
      shown+=("<RATCHET_STUDENT_KEY>")
      hide=0
    else
      shown+=("${a}")
      if [[ "${a}" == "--token" ]]; then hide=1; fi
    fi
  done
  echo "+ ${shown[*]}" >&2
}

run_or_show() {  # start and stop have no --dry-run, so DRY_RUN=1 prints them instead of running them
  show "$@"
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "DRY_RUN=1: not run" >&2
  else
    "$@"
  fi
}

record() {  # best effort: a missing ledger, Python or package must not fail the deploy
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "DRY_RUN=1: endpoint $1 not recorded" >&2
    return 0
  fi
  "${PY}" -m ratchet.cost endpoint "$1" --source deploy \
    || echo "note: endpoint $1 not recorded; run 'python -m ratchet.cost endpoint $1' where RATCHET_DB is" >&2
}

# Reads `nebius ai endpoint list --format json` on stdin and prints "<id> <state>" for the one endpoint whose
# metadata.<by> is <want>, or nothing when none is. The list is {"items": [...], "next_page_token": ...} and each
# endpoint carries metadata.id, metadata.name and status.state (nebius/api: nebius/ai/v1/endpoint.proto and
# endpoint_service.proto, read Oct 21, 2026); an empty list may print {} or nothing. A name missing from a list with
# more pages is an error, not "no endpoint": `start` would otherwise create a second one.
LOOKUP_PY='
import json
import sys

want, by = sys.argv[1], sys.argv[2]
raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except ValueError:
    sys.exit("error: nebius ai endpoint list --format json did not print JSON")
items = (data.get("items") or []) if isinstance(data, dict) else data
if not isinstance(items, list):
    sys.exit("error: the endpoint list has no items list")


def field(item, section, key):
    part = item.get(section)
    value = part.get(key) if isinstance(part, dict) else item.get(key)
    return value if isinstance(value, str) else ""


found = [item for item in items if isinstance(item, dict) and field(item, "metadata", by) == want]
if len(found) > 1:
    ids = ", ".join(field(item, "metadata", "id") for item in found)
    sys.exit(f"error: {len(found)} endpoints have the {by} {want} ({ids}); set RATCHET_ENDPOINT_ID")
if found:
    endpoint_id = field(found[0], "metadata", "id")
    if not endpoint_id or any(c.isspace() for c in endpoint_id):
        sys.exit(f"error: the endpoint with the {by} {want} has no usable metadata.id")
    print(endpoint_id, field(found[0], "status", "state").upper() or "UNKNOWN")
elif isinstance(data, dict) and (data.get("next_page_token") or data.get("nextPageToken")):
    sys.exit(f"error: no endpoint with the {by} {want} on the first page of the list; set RATCHET_ENDPOINT_ID")
'

lookup() {  # lookup name|id <value>: sets ENDPOINT_ID and ENDPOINT_STATE, both empty when no endpoint matches
  local by="$1" want="$2" listing found
  show nebius ai endpoint list --format json
  listing="$(nebius ai endpoint list --format json)" \
    || die 1 "nebius ai endpoint list failed; set RATCHET_ENDPOINT_ID to start or stop without the lookup"
  command -v "${PY}" >/dev/null 2>&1 \
    || die 1 "${PY} not found: set RATCHET_PYTHON to a Python 3, or RATCHET_ENDPOINT_ID to skip the lookup"
  found="$(printf '%s' "${listing}" | "${PY}" -I -c "${LOOKUP_PY}" "${want}" "${by}")" \
    || die 1 "could not find the endpoint's id in the list (see above)"
  ENDPOINT_ID="${found%% *}"
  ENDPOINT_STATE="${found#* }"
}

resolve() {  # the endpoint start and stop act on: RATCHET_ENDPOINT_ID as given (state unknown), or found by name
  if [[ -n "${RATCHET_ENDPOINT_ID:-}" ]]; then
    ENDPOINT_ID="${RATCHET_ENDPOINT_ID}"
    ENDPOINT_STATE=""
  else
    lookup name "${NAME}"
  fi
}

create() {  # create [checked]: "checked" when the caller has already looked the name up
  : "${RATCHET_BUCKET_ID:?set RATCHET_BUCKET_ID (bucket with /adapters, mounted at /data)}"
  : "${RATCHET_STUDENT_KEY:?set RATCHET_STUDENT_KEY (token clients must send)}"
  # The vLLM arguments are one string the CLI splits: a space or a quote in a value would break it.
  [[ "${BASE}" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die 2 "bad RATCHET_STUDENT_BASE_MODEL: ${BASE}"
  [[ "${MAX_LEN}" =~ ^[1-9][0-9]*$ ]] || die 2 "RATCHET_SERVE_MAX_LEN must be a positive whole number, not ${MAX_LEN}"
  [[ "${MAX_SEQS}" =~ ^[1-9][0-9]*$ ]] || die 2 "RATCHET_SERVE_MAX_SEQS must be a positive whole number, not ${MAX_SEQS}"
  [[ "${TEMPLATE_KWARGS}" != *"'"* ]] || die 2 "RATCHET_SERVE_TEMPLATE_KWARGS must not contain a single quote"
  [[ -z "${PARSER_PLUGIN}" || "${PARSER_PLUGIN}" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || die 2 "RATCHET_REASONING_PARSER_PLUGIN must be an absolute path in the container (/data/...), not ${PARSER_PLUGIN}"
  if [[ "${1:-}" != "checked" ]]; then
    lookup name "${NAME}"
    if [[ -n "${ENDPOINT_ID}" ]]; then
      die 1 "an endpoint named ${NAME} already exists (${ENDPOINT_ID}, ${ENDPOINT_STATE}): use start, or set RATCHET_ENDPOINT_NAME"
    fi
  fi
  # vLLM parses the value with json.loads at startup: a typo would fail the endpoint after the GPU is provisioned.
  if [[ -n "${TEMPLATE_KWARGS}" ]]; then
    "${PY}" -I -c 'import json, sys; sys.exit(not isinstance(json.loads(sys.argv[1]), dict))' "${TEMPLATE_KWARGS}" 2>/dev/null \
      || die 2 "RATCHET_SERVE_TEMPLATE_KWARGS must be a JSON object, such as {\"enable_thinking\":false}, not ${TEMPLATE_KWARGS}"
  fi

  # The model card's flags; a context cap far below its 262,144-token example (ticket prompts are short, and a
  # longer prompt gets vLLM's 400 and escalates); thinking off by default (each request also says so).
  local args="--model ${BASE} --trust-remote-code --enable-lora --max-lora-rank 16 --max-loras 4"
  args+=" --mamba_ssm_cache_dtype float32 --max-model-len ${MAX_LEN} --max-num-seqs ${MAX_SEQS}"
  if [[ -n "${TEMPLATE_KWARGS}" ]]; then
    args+=" --default-chat-template-kwargs '${TEMPLATE_KWARGS}'"
  fi
  if [[ -n "${PARSER_PLUGIN}" ]]; then
    args+=" --reasoning-parser-plugin ${PARSER_PLUGIN} --reasoning-parser nano_v3"
  fi
  local cmd=(nebius ai endpoint create
    --name "${NAME}"
    --image "${IMAGE}"
    --platform "${PLATFORM}" --preset "${PRESET}"
    --container-port 8000
    --shm-size 16Gi
    --volume "${RATCHET_BUCKET_ID}:/data"
    --auth token --token "${RATCHET_STUDENT_KEY}"
    --env VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
    --args "${args}")
  if [[ -n "${RATCHET_SUBNET_ID:-}" ]]; then cmd+=(--subnet-id "${RATCHET_SUBNET_ID}"); fi
  if [[ "${DRY_RUN}" == "1" ]]; then cmd+=(--dry-run); fi
  show "${cmd[@]}"
  "${cmd[@]}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "DRY_RUN=1: nebius validated the endpoint request; nothing was created." >&2
  fi
  record up
}

start() {
  resolve
  if [[ -z "${ENDPOINT_ID}" ]]; then
    echo "No endpoint named ${NAME} yet: creating it." >&2
    create checked
    return
  fi
  case "${ENDPOINT_STATE}" in
    RUNNING | STARTING | IMAGE_PULLING | PROVISIONING)
      echo "${NAME} (${ENDPOINT_ID}) is already ${ENDPOINT_STATE}." >&2
      ;;
    STOPPING) die 1 "${NAME} (${ENDPOINT_ID}) is STOPPING: run start again once it is STOPPED" ;;
    DELETING) die 1 "${NAME} (${ENDPOINT_ID}) is being deleted: run create once it is gone" ;;
    *) run_or_show nebius ai endpoint start --id "${ENDPOINT_ID}" ;;
  esac
  record up
}

stop() {
  resolve
  if [[ -z "${ENDPOINT_ID}" ]]; then
    die 1 "no endpoint named ${NAME}, nothing stopped. If it was deleted, record that where the ledger is: python -m ratchet.cost endpoint down"
  fi
  case "${ENDPOINT_STATE}" in
    STOPPED | STOPPING) echo "${NAME} (${ENDPOINT_ID}) is already ${ENDPOINT_STATE}." >&2 ;;
    *) run_or_show nebius ai endpoint stop --id "${ENDPOINT_ID}" ;;
  esac
  record down
}

status() {  # prints "<id> <state>"; exit 1 when there is no such endpoint
  if [[ -n "${RATCHET_ENDPOINT_ID:-}" ]]; then
    lookup id "${RATCHET_ENDPOINT_ID}"
    [[ -n "${ENDPOINT_ID}" ]] || die 1 "no endpoint with the id ${RATCHET_ENDPOINT_ID}"
  else
    lookup name "${NAME}"
    [[ -n "${ENDPOINT_ID}" ]] || die 1 "no endpoint named ${NAME} (bash deploy/student_endpoint.sh create)"
  fi
  echo "${ENDPOINT_ID} ${ENDPOINT_STATE}"
}

"${ACTION}"
