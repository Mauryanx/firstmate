#!/usr/bin/env bash
# Offline courier pickup: the test plays the courier (spool records, receipts)
# and Firstmate's side runs through its real CLI against a real pilot
# conversation transport. No courier, provider, network or live fleet write.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }
python3 -c 'import tomllib' 2>/dev/null || { echo 'skip: python3 has no tomllib'; exit 0; }
TMP_ROOT=$(fm_test_tmproot fm-courier-pickup)
# A real shell process holds the session lock, as in fm-inbox-conversation.test.sh.
cp "$(command -v bash)" "$TMP_ROOT/codex"
# shellcheck disable=SC2016 # The fixture shell expands its own arguments and PID.
"$TMP_ROOT/codex" -c 'PYTHONDONTWRITEBYTECODE=1 python3 "$1" "$2" "$3" "$$"; exit "$?"' fixture \
  "$ROOT/tests/fm-courier-pickup-cases.py" "$ROOT" "$TMP_ROOT" || fail 'courier pickup contract'
pass 'courier pickup: one turn per record, refusals, replies, polls, stages and latency'
