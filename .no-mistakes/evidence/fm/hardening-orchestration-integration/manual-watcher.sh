#!/usr/bin/env bash
set -eu
. tests/wake-helpers.sh
TMP_ROOT=$(fm_test_tmproot fm-manual-watcher)
code_root=${1:-$ROOT}
WATCH="$code_root/bin/fm-watch.sh"
DRAIN="$code_root/bin/fm-wake-drain.sh"
dir=$(make_case signal)
state="$dir/state"
fakebin="$dir/fakebin"
status_file="$state/task.status"
printf 'blocked: first\n' > "$status_file"
start=$(date +%s)
PATH="$fakebin:$PATH" FM_STATE_OVERRIDE="$state" FM_POLL=1 FM_SIGNAL_GRACE=1 \
  FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$WATCH" > "$dir/first.out" 2> "$dir/first.err" &
wait_for_exit "$!" 200
elapsed=$(( $(date +%s) - start ))
grep -F "signal: $status_file" "$dir/first.out"
printf 'First watcher announced the actionable signal in %s seconds.\n' "$elapsed"
FM_STATE_OVERRIDE="$state" "$DRAIN" > "$dir/drain.out" 2> "$dir/drain.err"
grep -F "$status_file" "$dir/drain.out"
sequence=$(sed -n 's/^WAKE_ACK_REQUIRED:.*--ack-through \([0-9][0-9]*\) --recovery-generation [A-Za-z0-9._-][A-Za-z0-9._-]*$/\1/p' "$dir/drain.err")
generation=$(sed -n 's/^WAKE_ACK_REQUIRED:.*--ack-through [0-9][0-9]* --recovery-generation \([A-Za-z0-9._-][A-Za-z0-9._-]*\)$/\1/p' "$dir/drain.err")
FM_STATE_OVERRIDE="$state" "$DRAIN" --ack-through "$sequence" --recovery-generation "$generation"
printf 'done: second\n' >> "$status_file"
start=$(date +%s)
PATH="$fakebin:$PATH" FM_STATE_OVERRIDE="$state" FM_POLL=1 FM_SIGNAL_GRACE=1 \
  FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$WATCH" > "$dir/second.out" 2> "$dir/second.err" &
wait_for_exit "$!" 200
elapsed=$(( $(date +%s) - start ))
grep -F "signal: $status_file" "$dir/second.out"
printf 'Restarted watcher caught the status written while stopped in %s seconds.\n' "$elapsed"
