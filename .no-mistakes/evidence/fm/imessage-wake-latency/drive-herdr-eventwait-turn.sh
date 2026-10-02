#!/usr/bin/env bash
# Manual live driver: a texted turn arrives while the REAL watcher sits in the
# herdr event wait (an isolated fm-lab-* herdr session with one recorded task
# pane), in a throwaway FM_HOME. Usage: drive-herdr-eventwait-turn.sh <repo-root> <label>
set -u
ROOT=$1 LABEL=$2
fail() { echo "[$LABEL] FAIL: $*"; exit 1; }
# shellcheck source=/dev/null
. "$ROOT/tests/herdr-test-safety.sh"
herdr_forget_inherited_pane
SESSION="fm-lab-convturn-$$"; export HERDR_SESSION="$SESSION"
WORK=$(mktemp -d /tmp/fm-herdr-turn.XXXXXX)
HOME_DIR="$WORK/home"; STATE="$HOME_DIR/state"; OUT="$WORK/watch.out"
WPID=
cleanup_all() {
  [ -z "$WPID" ] || kill "$WPID" 2>/dev/null
  herdr_safe_stop_and_delete "$SESSION"
  rm -rf "$WORK"
}
trap cleanup_all EXIT
fm_herdr_lab_prepare "$SESSION" || fail "lab prepare"
# shellcheck source=/dev/null
. "$ROOT/bin/fm-backend.sh"; fm_backend_source herdr || fail "backend source"
fm_backend_herdr_events_capable "$SESSION" || fail "herdr not events-capable"
CONTAINER_RAW=$(fm_backend_herdr_container_ensure /tmp) || fail container
IDS=$(fm_backend_herdr_create_task "${CONTAINER_RAW%%$'\t'*}" fm-convturn /tmp "${CONTAINER_RAW#*$'\t'}") || fail create_task
read -r _TAB PANE <<< "$IDS"
fm_herdr_lab_cli "$SESSION" pane report-agent "$PANE" --source fm-convturn --agent claude --state working >/dev/null 2>&1 || fail report-agent

OWNER="$WORK/codex"; cp "$(command -v bash)" "$OWNER"
envx() { env -u FM_STATE_OVERRIDE -u FM_DATA_OVERRIDE -u FM_CONFIG_OVERRIDE -u FM_WAKE_QUEUE -u FM_WAKE_QUEUE_LOCK FM_HOME="$HOME_DIR" HERDR_SESSION="$SESSION" "$@"; }
transport() { envx "$ROOT/bin/fm-inbox.sh" conversation "$1" <<< "$2"; }
transport lab-init '{"speech_catalog": {"ack": "On it."}}' >/dev/null || fail lab-init
CRED=$("$OWNER" -c 'printf "%s\n" "$$" > "$1/state/.lock"
  env -u FM_STATE_OVERRIDE -u FM_DATA_OVERRIDE -u FM_CONFIG_OVERRIDE -u FM_WAKE_QUEUE -u FM_WAKE_QUEUE_LOCK FM_HOME="$1" \
    "$2/bin/fm-inbox.sh" conversation bind <<< "{\"conversation_id\": \"text\", \"authenticated_principal\": \"captain\"}"' _ "$HOME_DIR" "$ROOT" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["credential"])') || fail bind
printf 'window=%s\nbackend=herdr\nkind=ship\n' "$SESSION:$PANE" > "$STATE/convturn.meta"
ts() { date -u +%H:%M:%S.%3N; }
before=$(ls -d "${TMPDIR:-/tmp}"/fm-herdr-eventwait.* 2>/dev/null | wc -l)
envx FM_POLL=60 FM_SIGNAL_GRACE=1 FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$ROOT/bin/fm-watch.sh" > "$OUT" 2>"$WORK/watch.err" &
WPID=$!
sleep 6
kill -0 "$WPID" 2>/dev/null || fail "watcher closed before any turn: $(cat "$OUT") $(tail -5 "$WORK/watch.err")"
echo "[$LABEL] $(ts) watcher pid=$WPID idle over herdr pane $SESSION:$PANE (FM_POLL=60)"
echo "[$LABEL] watcher children (expect the event wait + sentinel):"
pstree -a -p "$WPID" 2>/dev/null | sed 's/^/    /' | head -12
START=$(date +%s.%N)
transport capture "{\"conversation_id\": \"text\", \"credential\": \"$CRED\", \"turn_id\": \"t1\", \"request_id\": \"r1\", \"committed_transcript\": \"Are you there?\", \"revision\": 1, \"previous_turn_id\": null, \"created_at\": \"2026-10-02T04:24:29Z\"}" >/dev/null || fail capture
echo "[$LABEL] $(ts) texted turn filed"
while kill -0 "$WPID" 2>/dev/null; do
  awk -v a="$(date +%s.%N)" -v b="$START" 'BEGIN{exit !(a-b>75)}' && { echo "[$LABEL] gave up after 75s"; break; }
  sleep 0.1
done
END=$(date +%s.%N); WPID=
echo "[$LABEL] $(ts) watcher exited; latency from text to wake: $(awk -v a="$END" -v b="$START" 'BEGIN{printf "%.1f", a-b}')s"
echo "[$LABEL] watcher stdout:"; sed 's/^/    /' "$OUT"
sleep 2
after=$(ls -d "${TMPDIR:-/tmp}"/fm-herdr-eventwait.* 2>/dev/null | wc -l)
echo "[$LABEL] fm-herdr-eventwait scratch dirs before=$before after=$after (declined review-1 concern)"
echo "[$LABEL] processes still carrying this home's FM_HOME after exit: $(grep -lz "^FM_HOME=$HOME_DIR\$" /proc/[0-9]*/environ 2>/dev/null | while read -r f; do p=${f#/proc/}; p=${p%%/*}; tr '\0' ' ' < /proc/$p/cmdline; echo; done | grep -v '^$' | sed 's/^/  /')"
