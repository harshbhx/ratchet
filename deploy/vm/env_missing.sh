#!/usr/bin/env bash
# Names of the settings in .env.vm.example that .env does not set, one per line (names only: never a value, which may be a
# key). A setting missing from .env takes the gateway's own default, which is not always the example's; update.sh prints
# the list after a pull, so a setting a new release added is not missed.
#   env_missing.sh [EXAMPLE_FILE] [ENV_FILE]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
example="${1:-$HERE/.env.vm.example}"
env_file="${2:-$HERE/.env}"
[[ -r "$example" && -r "$env_file" ]] || exit 0
grep -oE '^[A-Z][A-Z0-9_]*=' "$example" | tr -d '=' | while IFS= read -r name; do
  grep -qE "^${name}=" "$env_file" || echo "$name"
done
