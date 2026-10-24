#!/usr/bin/env bash
# Set one variable in deploy/vm/.env: the value comes on stdin, so it never appears in a command line, a log or
# `ps`. Used by deploy/nebius/nebius.sh over ssh (as the ratchet user); safe to run by hand.
#   printf %s "$VALUE" | sudo -u ratchet /opt/ratchet/deploy/vm/set_env.sh RATCHET_STUDENT_KEY
# Replaces the variable's line, or appends one; the file's mode and owner stay.  ENV_FILE overrides the path.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$HERE/.env}"
NAME="${1:?usage: printf %s \"\$VALUE\" | set_env.sh NAME}"
[[ "$NAME" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "error: bad variable name: $NAME" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE not found" >&2; exit 1; }

VALUE="$(cat)"
[[ "$VALUE" != *$'\n'* && "$VALUE" != *$'\r'* ]] || { echo "error: the value must be one line" >&2; exit 2; }

TMP="$(mktemp "$ENV_FILE.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == "$NAME="* ]]; then
    if [[ "$found" == 0 ]]; then printf '%s=%s\n' "$NAME" "$VALUE"; fi
    found=1
  else
    printf '%s\n' "$line"
  fi
done <"$ENV_FILE" >"$TMP"
if [[ "$found" == 0 ]]; then printf '%s=%s\n' "$NAME" "$VALUE" >>"$TMP"; fi
cat "$TMP" >"$ENV_FILE"  # keep the inode, mode and owner
echo "set $NAME in $ENV_FILE" >&2
