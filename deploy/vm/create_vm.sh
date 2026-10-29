#!/usr/bin/env bash
# Create the Ratchet demo VM on Nebius AI Cloud: boot disk, CPU VM with a static public IP,
# and first-boot setup from cloud-init.yaml (Docker, firewall, clone, docker compose up).
#
#   export NEBIUS_PROJECT_ID=project-...            # required (parent ID)
#   export SSH_PUBLIC_KEY_PATH=~/.ssh/id_ed25519.pub # default shown
#   bash deploy/vm/create_vm.sh                      # DRY_RUN=1 prints the commands instead
#
# Optional: NEBIUS_SUBNET_ID (default: first subnet in the project), RATCHET_VM_NAME (ratchet-host),
# RATCHET_VM_PLATFORM (cpu-e2), RATCHET_VM_PRESET (2vcpu-8gb), RATCHET_VM_DISK_GB (30),
# RATCHET_SSH_USER (ubuntu), RATCHET_REPO_URL, RATCHET_DOMAIN (empty: <ip>.sslip.io).
#
# Flags follow docs.nebius.com as of Oct 2026 (CLI reference for `compute disk create` and
# `compute instance create`, Compute quickstart). Lines marked VERIFY ON DAY 1 are not confirmed.
# Guide: docs/DEPLOY_NEBIUS_VM.md
set -euo pipefail
shopt -u patsub_replacement 2>/dev/null || true  # bash 5.2+: keep '&' literal in ${var//x/y}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${DRY_RUN:-0}"

PARENT_ID="${NEBIUS_PROJECT_ID:-${NEBIUS_PARENT_ID:-}}"
[[ -n "$PARENT_ID" ]] || { echo "Set NEBIUS_PROJECT_ID (your Nebius project ID, used as --parent-id)." >&2; exit 1; }
VM_NAME="${RATCHET_VM_NAME:-ratchet-host}"
# cpu-e2 (Intel Ice Lake) exists only in eu-north1; use cpu-d3 (AMD EPYC Genoa) in other regions.
PLATFORM="${RATCHET_VM_PLATFORM:-cpu-e2}"
PRESET="${RATCHET_VM_PRESET:-2vcpu-8gb}"
DISK_GB="${RATCHET_VM_DISK_GB:-30}"
IMAGE_FAMILY="ubuntu24.04-driverless"  # recommended image for cpu-d3 and cpu-e2
SSH_KEY_PATH="${SSH_PUBLIC_KEY_PATH:-$HOME/.ssh/id_ed25519.pub}"
SSH_USER="${RATCHET_SSH_USER:-ubuntu}"
REPO_URL="${RATCHET_REPO_URL:-https://github.com/harshbhx/ratchet.git}"
DOMAIN="${RATCHET_DOMAIN:-}"

die() { echo "error: $*" >&2; exit 1; }

# --- checks --------------------------------------------------------------------
if [[ "$DRY_RUN" != "1" ]]; then
  command -v nebius >/dev/null || die "nebius CLI not found. Install: curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash (Ubuntu or macOS; on Windows use WSL or Cloud Shell)"
  command -v jq >/dev/null || die "jq not found (needed to read the public IP)"
fi
[[ -r "$SSH_KEY_PATH" ]] || die "SSH public key not found at $SSH_KEY_PATH (set SSH_PUBLIC_KEY_PATH)"
SSH_KEY="$(head -n1 "$SSH_KEY_PATH")"
[[ "$SSH_KEY" =~ ^(ssh-|ecdsa-|sk-) ]] || die "$SSH_KEY_PATH does not look like an OpenSSH public key"
[[ "$SSH_USER" != "root" && "$SSH_USER" != "admin" ]] || die "Nebius reserves the user names root and admin"
[[ "$SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "RATCHET_SSH_USER must be a valid Linux user name"
[[ "$PRESET" =~ ^[0-9]+vcpu-[0-9]+gb$ ]] || die "RATCHET_VM_PRESET looks wrong: $PRESET (expected e.g. 2vcpu-8gb)"

# --- cloud-init user data ------------------------------------------------------
USER_DATA="$(<"$HERE/cloud-init.yaml")"
USER_DATA="${USER_DATA//__SSH_USER__/$SSH_USER}"
# The key sits in single quotes in the YAML: a quote in its comment is doubled, so it cannot end the string.
SSH_KEY_YAML="${SSH_KEY//\'/\'\'}"
USER_DATA="${USER_DATA//__SSH_PUBLIC_KEY__/$SSH_KEY_YAML}"
USER_DATA="${USER_DATA//__REPO_URL__/$REPO_URL}"
USER_DATA="${USER_DATA//__RATCHET_DOMAIN__/$DOMAIN}"

run() {
  # Print the command (without the user data, which may hold a token) and run it unless DRY_RUN=1.
  local shown=() a
  for a in "$@"; do
    if [[ "$a" == "#cloud-config"* ]]; then shown+=("<cloud-init.yaml>"); else shown+=("$a"); fi
  done
  echo "+ ${shown[*]}" >&2
  if [[ "$DRY_RUN" == "1" ]]; then echo "dry-run-id"; else "$@"; fi
}

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN=1: nothing will be created. Rendered user data (first lines):" >&2
  printf '%s\n' "$USER_DATA" | sed -n '1,3p;/^users:/,/^package_update/p' >&2
fi

# --- existing VM? ----------------------------------------------------------------
if [[ "$DRY_RUN" != "1" ]] && nebius compute instance get-by-name --parent-id "$PARENT_ID" --name "$VM_NAME" --format json >/dev/null 2>&1; then
  die "a VM named $VM_NAME already exists. Delete it first, or set RATCHET_VM_NAME."
fi

# --- subnet ------------------------------------------------------------------------
SUBNET_ID="${NEBIUS_SUBNET_ID:-}"
if [[ -z "$SUBNET_ID" ]]; then
  # `vpc subnet list` takes --parent-id (the Compute quickstart omits it and uses the profile's project).
  SUBNET_ID="$(run nebius vpc subnet list --parent-id "$PARENT_ID" --format jsonpath='{.items[0].metadata.id}')"
fi
[[ -n "$SUBNET_ID" ]] || die "no subnet found; set NEBIUS_SUBNET_ID (nebius vpc subnet list)"
echo "Subnet: $SUBNET_ID" >&2

# --- boot disk -------------------------------------------------------------------
# A separate disk (not a managed one) survives `instance delete`, so the VM can be recreated
# without losing Docker volumes. Delete it yourself at teardown.
DISK_ID="$(run nebius compute disk create \
  --parent-id "$PARENT_ID" \
  --name "${VM_NAME}-boot" \
  --type network_ssd \
  --size-gibibytes "$DISK_GB" \
  --block-size-bytes 4096 \
  --source-image-family-image-family "$IMAGE_FAMILY" \
  --format jsonpath='{.metadata.id}')"
[[ -n "$DISK_ID" ]] || die "disk creation returned no ID"
echo "Boot disk: $DISK_ID" >&2

# --- VM ----------------------------------------------------------------------------
# "static": true keeps the public IP while the VM is stopped (released on delete).
NET_IFACES="[{\"name\": \"eth0\", \"subnet_id\": \"${SUBNET_ID}\", \"ip_address\": {}, \"public_ip_address\": {\"static\": true}}]"
VM_ID="$(run nebius compute instance create \
  --parent-id "$PARENT_ID" \
  --name "$VM_NAME" \
  --resources-platform "$PLATFORM" \
  --resources-preset "$PRESET" \
  --boot-disk-existing-disk-id "$DISK_ID" \
  --boot-disk-attach-mode READ_WRITE \
  --cloud-init-user-data "$USER_DATA" \
  --network-interfaces "$NET_IFACES" \
  --format jsonpath='{.metadata.id}')"
[[ -n "$VM_ID" ]] || die "VM creation returned no ID"
echo "VM: $VM_ID" >&2

# --- public IP -----------------------------------------------------------------------
IP=""
if [[ "$DRY_RUN" == "1" ]]; then
  IP="203.0.113.7"
else
  for _ in $(seq 1 24); do
    IP="$(nebius compute instance get --id "$VM_ID" --format json \
      | jq -r '.status.network_interfaces[0].public_ip_address.address // empty | split("/")[0]')" || true
    [[ -n "$IP" ]] && break
    sleep 5
  done
fi
[[ -n "$IP" ]] || die "VM created ($VM_ID) but no public IP yet; check: nebius compute instance get --id $VM_ID"

SHOWN_DOMAIN="${DOMAIN:-${IP}.sslip.io}"
cat <<EOF

Created ${VM_NAME}
  VM ID:      ${VM_ID}
  Boot disk:  ${DISK_ID}
  Public IP:  ${IP}
  Domain:     ${SHOWN_DOMAIN}

Next steps
  1. Wait 5-10 minutes for first boot (packages, Docker, image build, certificate).
     Watch it:  ssh ${SSH_USER}@${IP} 'sudo tail -f /var/log/ratchet-bootstrap.log'
  2. Open https://${SHOWN_DOMAIN}/   then /scout and /dashboard.
     The simulation (mock models; run days, break a deploy, watch the rollback) is at https://sim.${SHOWN_DOMAIN}/
     (sslip.io resolves it; your own domain needs a DNS record for sim.<name> first).
  3. Own domain: add a DNS A record, e.g. gateway.example.com -> ${IP}, then on the VM:
       sudo -u ratchet sed -i 's|^RATCHET_DOMAIN=.*|RATCHET_DOMAIN=gateway.example.com|' /opt/ratchet/deploy/vm/.env
       cd /opt/ratchet/deploy/vm && sudo -u ratchet docker compose up -d
  4. Live mode, keys and the budget guard: docs/DEPLOY_NEBIUS_VM.md

Teardown (stops all charges for these resources):
  nebius compute instance delete --id ${VM_ID}
  nebius compute disk delete --id ${DISK_ID}
EOF
