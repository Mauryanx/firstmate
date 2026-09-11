#!/usr/bin/env bash
# tests/fm-wake-drain-voice-hold.test.sh - an acknowledgement never retires a
# captain voice wake whose conversation request is still saved. The incident
# this pins: on 2026-09-10 every spoken turn's wake was consumed while its
# request stayed saved, so nothing was ever spoken back. The turn is captured
# through the real transport (bin/fm-inbox.sh conversation) into a lab home so
# the drain's "still saved" signal is the transport's own durable note record,
# and the guarantee is shown to end exactly when the request is accepted.
# An ordinary row in the same drain is acknowledged as before.
set -u

# shellcheck source=tests/wake-helpers.sh
. "$(dirname "${BASH_SOURCE[0]}")/wake-helpers.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }

DRAIN="$ROOT/bin/fm-wake-drain.sh"
CLI="$ROOT/bin/fm-inbox.sh"
TMP_ROOT=$(fm_test_tmproot fm-wake-drain-voice-hold-tests)
TYPED_KEY='inbox:1757000000-typed1'

# The session-lock seam needs a harness-named ancestor; a copy of bash named
# codex supplies one, exactly as tests/fm-inbox-conversation.test.sh does.
cp "$(command -v bash)" "$TMP_ROOT/codex"

# Each owning session is a fresh codex process that writes its own pid into
# the home's session lock and then runs one owner command against the CLI.
cat > "$TMP_ROOT/bind-session.sh" <<'SH'
set -eu
home=$1 cli=$2
printf '%s\n' "$$" > "$home/state/.lock"
printf '%s' '{"conversation_id":"c","authenticated_principal":"captain"}' \
  | FM_HOME="$home" "$cli" conversation bind > "$home/bind.json"
SH
cat > "$TMP_ROOT/accept-session.sh" <<'SH'
set -eu
home=$1 cli=$2
printf '%s\n' "$$" > "$home/state/.lock"
printf '%s' '{"conversation_id":"c"}' \
  | FM_HOME="$home" "$cli" conversation accept > "$home/accept.json"
SH

as_session() {  # <session-script> <home>
  "$TMP_ROOT/codex" "$TMP_ROOT/$1" "$2" "$CLI"
}

json_field() {  # <file> <key-path>
  python3 -c '
import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2].split("."):
    value = value[key]
print(value if not isinstance(value, bool) else str(value).lower())
' "$1" "$2"
}

queue_has_key() {  # <state> <key>
  awk -F '\t' -v key="$2" 'NF >= 5 && $4 == key { found = 1 } END { exit found ? 0 : 1 }' "$1/.wake-queue"
}

test_ack_holds_a_voice_row_while_its_request_is_saved() {
  local home state out err ack_err credential note voice_key
  home="$TMP_ROOT/hold/home"
  mkdir -p "$TMP_ROOT/hold"
  state="$home/state"
  out="$TMP_ROOT/hold/drain.out"
  err="$TMP_ROOT/hold/drain.err"
  ack_err="$TMP_ROOT/hold/ack.err"

  printf '%s' '{"speech_catalog":{"answer":"Yes, it is green."}}' \
    | FM_HOME="$home" "$CLI" conversation lab-init > /dev/null || fail "lab-init failed"
  as_session bind-session.sh "$home" || fail "bind by the first owning session failed"
  credential=$(json_field "$home/bind.json" credential)
  printf '{"conversation_id":"c","credential":"%s","turn_id":"t1","request_id":"r1","committed_transcript":"Is the deploy green?","revision":1,"previous_turn_id":null,"created_at":"2026-09-11T00:00:00Z"}' "$credential" \
    | FM_HOME="$home" "$CLI" conversation capture > "$TMP_ROOT/hold/capture.json" || fail "capture failed"
  [ "$(json_field "$TMP_ROOT/hold/capture.json" state)" = saved ] || fail "captured request is not saved"
  note=$(cd "$state/inbox" && ls vc-*.note) || fail "capture wrote no pending vc- note"
  voice_key="inbox:${note%.note}"
  queue_has_key "$state" "$voice_key" || fail "capture appended no voice wake"
  append_wake "$state" check "$TYPED_KEY" "check: captain inbox note 1757000000-typed1 - typed note" \
    || fail "ordinary check wake append failed"

  FM_STATE_OVERRIDE="$state" "$DRAIN" > "$out" 2> "$err" || fail "drain failed: $(cat "$err")"
  grep -q '^VOICE:' "$out" || fail "the captured turn was not presented under VOICE: $(cat "$out")"
  ack_drain_err "$state" "$err" 2> "$ack_err" || fail "acknowledgement failed: $(cat "$ack_err")"
  queue_has_key "$state" "$voice_key" || fail "the voice row was retired while its request was still saved"
  ! queue_has_key "$state" "$TYPED_KEY" || fail "the ordinary row beside the voice row was not acknowledged"
  grep -q 'held 1 voice wake row' "$ack_err" || fail "the hold was not reported: $(cat "$ack_err")"
  [ -f "$state/inbox/$note" ] || fail "the acknowledgement moved the pending note"
  pass "an acknowledgement holds a voice row whose request is still saved and retires the ordinary row beside it"

  # Re-presentation, still under VOICE, with the same acknowledgement command.
  FM_STATE_OVERRIDE="$state" "$DRAIN" > "$out" 2> "$err" || fail "second drain failed: $(cat "$err")"
  grep -q '^VOICE:' "$out" || fail "the held row was not presented again under VOICE: $(cat "$out")"
  awk -F '\t' -v key="$voice_key" 'NF == 5 && $4 == key { found = 1 } END { exit found ? 0 : 1 }' "$out" \
    || fail "the held row was not presented again: $(cat "$out")"
  pass "a held voice row is presented again by the next drain"

  # Accepting the request through the transport ends the hold. The accepting
  # session is a later one than the session that bound the conversation.
  as_session accept-session.sh "$home" || fail "accept by a later owning session failed"
  [ "$(json_field "$home/accept.json" dispatch)" = true ] || fail "accept did not dispatch the saved request"
  [ "$(json_field "$home/accept.json" input.request_id)" = r1 ] || fail "accept dispatched the wrong request"
  [ -f "$state/inbox/handled/$note" ] || fail "acceptance did not move the note to handled/"
  ack_drain_err "$state" "$err" 2> "$ack_err" || fail "acknowledgement after acceptance failed: $(cat "$ack_err")"
  ! queue_has_key "$state" "$voice_key" || fail "the voice row stayed queued after its request was accepted"
  [ ! -s "$state/.wake-queue" ] || fail "rows remained queued after acceptance and acknowledgement: $(cat "$state/.wake-queue")"
  ! grep -q 'held' "$ack_err" || fail "a hold was reported after the request was accepted: $(cat "$ack_err")"
  pass "the hold ends once the request is accepted and the ordinary acknowledgement retires the row"
}

test_ack_holds_a_voice_row_while_its_request_is_saved
