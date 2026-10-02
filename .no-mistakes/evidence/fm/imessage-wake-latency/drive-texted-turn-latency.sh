#!/usr/bin/env bash
# Manual live driver: a texted turn filed by the real conversation transport
# against a real idle watcher (FM_POLL=60) in a throwaway FM_HOME.
# Usage: drive-texted-turn-latency.sh <repo-root> <label> [scenario]
# scenario: idle (default) | lockheld
set -u
ROOT=$1 LABEL=$2 SCEN=${3:-idle}
WORK=$(mktemp -d /tmp/fm-latency.XXXXXX)
HOME_DIR="$WORK/home"; STATE="$HOME_DIR/state"; OUT="$WORK/watch.out"
OWNER="$WORK/codex"; cp "$(command -v bash)" "$OWNER"
envx() { env -u FM_STATE_OVERRIDE -u FM_DATA_OVERRIDE -u FM_CONFIG_OVERRIDE -u FM_WAKE_QUEUE -u FM_WAKE_QUEUE_LOCK FM_HOME="$HOME_DIR" "$@"; }
transport() { envx "$ROOT/bin/fm-inbox.sh" conversation "$1" <<< "$2"; }
owner() {
  "$OWNER" -c 'printf "%s\n" "$$" > "$1/state/.lock"
    env -u FM_STATE_OVERRIDE -u FM_DATA_OVERRIDE -u FM_CONFIG_OVERRIDE -u FM_WAKE_QUEUE -u FM_WAKE_QUEUE_LOCK FM_HOME="$1" \
      "$2/bin/fm-inbox.sh" conversation "$3" <<< "$4"' _ "$HOME_DIR" "$ROOT" "$1" "$2"
}
ts() { date -u +%H:%M:%S.%3N; }
transport lab-init '{"speech_catalog": {"ack": "On it."}}' >/dev/null || { echo "lab-init failed"; exit 1; }
CRED=$(owner bind '{"conversation_id": "text", "authenticated_principal": "captain"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["credential"])')
echo "[$LABEL] $(ts) home=$HOME_DIR"
envx FM_POLL=60 FM_SIGNAL_GRACE=1 FM_CHECK_INTERVAL=999999 FM_HEARTBEAT=999999 "$ROOT/bin/fm-watch.sh" > "$OUT" 2>"$WORK/watch.err" &
WPID=$!
for _ in $(seq 100); do [ -e "$STATE/.last-watcher-beat" ] && break; sleep 0.1; done
sleep 2
kill -0 "$WPID" 2>/dev/null && echo "[$LABEL] $(ts) watcher pid=$WPID idle, polling every 60s"
if [ "$SCEN" = lockheld ]; then
  # Adversarial: pretend the watcher's main shell holds a state lock; delivery must stand down.
  REALPID=$(cat "$STATE/.watch.lock/pid")
  mkdir -p "$STATE/.test-fake.lock"; printf '%s\n' "$REALPID" > "$STATE/.test-fake.lock/pid"
  echo "[$LABEL] $(ts) planted a state lock owned by the watcher pid $REALPID (bg pid was $WPID)"
fi
START=$(date +%s.%N)
transport capture "{\"conversation_id\": \"text\", \"credential\": \"$CRED\", \"turn_id\": \"t1\", \"request_id\": \"r1\", \"committed_transcript\": \"Are you there?\", \"revision\": 1, \"previous_turn_id\": null, \"created_at\": \"2026-10-02T04:24:29Z\"}" >/dev/null \
  || { echo "capture refused"; kill "$WPID"; exit 1; }
echo "[$LABEL] $(ts) texted turn filed; queue row: $(awk -F '\t' '$4 ~ /^inbox:vc-/ {print $3, $4}' "$STATE/.wake-queue")"
LIMIT=${LIMIT:-70}
if [ "$SCEN" = lockheld ]; then
  for _ in $(seq 40); do kill -0 "$WPID" 2>/dev/null || { echo "[$LABEL] $(ts) watcher exited while lock still held"; break; }; sleep 0.1; done
  if kill -0 "$WPID" 2>/dev/null; then echo "[$LABEL] $(ts) still running after 4s with lock held (stood down, as required)"; else echo "[$LABEL] $(ts) EXITED while lock held: $(cat "$OUT")"; fi
  rm -rf "$STATE/.test-fake.lock"; echo "[$LABEL] $(ts) lock released"
fi
while kill -0 "$WPID" 2>/dev/null; do
  now=$(date +%s.%N)
  if awk -v a="$now" -v b="$START" -v l="$LIMIT" 'BEGIN{exit !(a-b>l)}'; then echo "[$LABEL] gave up after ${LIMIT}s"; kill "$WPID"; break; fi
  sleep 0.1
done
END=$(date +%s.%N)
echo "[$LABEL] $(ts) watcher exited; latency from text to wake: $(awk -v a="$END" -v b="$START" 'BEGIN{printf "%.1f", a-b}')s"
echo "[$LABEL] watcher stdout (the wake reason Firstmate sees):"; sed 's/^/    /' "$OUT"
echo "[$LABEL] seen markers: $(cd "$STATE" && ls -a | grep -c '^\.seen-conversation-' || true)"
sleep 1
echo "[$LABEL] processes still carrying this home's FM_HOME after exit: $(grep -lz "^FM_HOME=$HOME_DIR\$" /proc/[0-9]*/environ 2>/dev/null | while read -r f; do p=${f#/proc/}; p=${p%%/*}; tr '\0' ' ' < /proc/$p/cmdline; echo; done | grep -v '^$' | sed 's/^/  /')"
rm -rf "$WORK"
