#!/usr/bin/env bash
# Offline stage-one acceptance: public inbox entry point, real session-lock
# ownership checks, immutable identities, crash recovery, and mocked playback.
# No harness prompts, provider/device calls, external network, or live fleet writes.
# The transport is exercised through its public CLI only; no voice client,
# browser, or speech provider takes part, so nothing here establishes turn
# latency, recognition, or voice quality.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }
TMP_ROOT=$(fm_test_tmproot fm-inbox-conversation)
# A real shell process supplies the already-supported harness ancestry seam.
# This is a contract fixture, not a claim about any vendor's native session API.
cp "$(command -v bash)" "$TMP_ROOT/codex"
# shellcheck disable=SC2016 # The fixture shell expands its own arguments and PID.
"$TMP_ROOT/codex" -c 'python3 "$1" "$2" "$3" "$$"; exit "$?"' fixture \
  "$ROOT/tests/fm-inbox-conversation-cases.py" "$ROOT" "$TMP_ROOT" || fail 'conversation contract'
pass 'conversation contract: durable capture, session routing, reply and playback recovery'
