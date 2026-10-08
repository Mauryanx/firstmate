#!/usr/bin/env bash
# Offline brain-room client behavior through real CLI subprocesses.
# sudo is a fixture executable; no privilege, brain or provider call occurs.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }
TMP_ROOT=$(fm_test_tmproot fm-brain-desk)
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tests/fm-brain-desk-cases.py" "$ROOT" "$TMP_ROOT"
