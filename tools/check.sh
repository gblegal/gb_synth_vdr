#!/usr/bin/env bash
# Thin wrapper. All logic lives in synthvdr.qa so one implementation is tested.
set -euo pipefail
ROOM="${1:-.}"
shift || true
exec python3 -m synthvdr.qa --room "$ROOM" "$@"
