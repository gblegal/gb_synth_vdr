#!/usr/bin/env bash
# Thin wrapper. All logic lives in synthvdr.qa so one implementation is tested.
#
# The interpreter is resolved rather than assumed. This script is copied INTO
# each room, and a room is a directory of its own with no relationship to
# wherever synthvdr was installed — so `python3` on the PATH there is very often
# not the interpreter that has it. That used to surface as a raw
# ModuleNotFoundError traceback from inside the package, which is the first
# thing a new user meets and names neither the cause nor the fix.
set -euo pipefail
ROOM="${1:-.}"
shift || true

PY="${SYNTHVDR_PYTHON:-python3}"

if ! "$PY" -c 'import synthvdr.qa' >/dev/null 2>&1; then
    cat >&2 <<MSG
tools/check.sh: '$PY' cannot import synthvdr.

A room is a directory of its own, so the python on your PATH here is not
necessarily the one synthvdr is installed into. Either install it:

  pip install -e '<path-to-the-synth-vdr-plugin>[dev]'

or point this script at the interpreter that already has it:

  SYNTHVDR_PYTHON=/path/to/.venv/bin/python bash tools/check.sh "$ROOM"
MSG
    exit 1
fi

exec "$PY" -m synthvdr.qa --room "$ROOM" "$@"
