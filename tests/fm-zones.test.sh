#!/usr/bin/env bash
# Offline boundary-client behavior through real CLI subprocesses.
# sudo is a fixture executable; no privilege or provider call occurs.
# Optional FM_ZONES_COURIER_RELEASE runs parity against a reviewed read-only
# firstmate-voice release and its loopback-only provider fixture, never live keys.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }
TMP_ROOT=$(fm_test_tmproot fm-zones)
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tests/fm-zones-cases.py" "$ROOT" "$TMP_ROOT"
