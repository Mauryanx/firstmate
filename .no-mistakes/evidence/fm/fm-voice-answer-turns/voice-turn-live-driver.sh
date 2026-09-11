#!/usr/bin/env bash
# Live drive of the answer-voice-turn change: runs under a harness-named
# ancestor (a bash copy named codex) so the session-lock seam is real.
# $1 = repo root, $2 = temp root, $3 = evidence directory
set -u
ROOT=$1 TMP=$2 EVID=$3
CLI="$ROOT/bin/fm-inbox.sh"
DRAIN="$ROOT/bin/fm-wake-drain.sh"
HOME_DIR="$TMP/home"
STATE="$HOME_DIR/state"
export FM_HOME="$HOME_DIR"
# Drain-only environment, mirroring tests/wake-helpers.sh: a non-git tangle root
# and a recorder for the wedge alarm so nothing can post a real notification.
mkdir -p "$TMP/tangle-root"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/rec"; chmod +x "$TMP/rec"
drain() {  # [args...]
  FM_HOME= FM_STATE_OVERRIDE="$STATE" FM_ROOT_OVERRIDE="$TMP/tangle-root" FM_WEDGE_ALARM_EXEC="$TMP/rec" \
    "$DRAIN" "$@"
}
PASS=0; FAIL=0
say() { printf '\n%s\n' "$*"; }
hr() { printf '\n==================== %s ====================\n' "$*"; }
check() {  # <label> <condition-exit>
  if [ "$2" -eq 0 ]; then PASS=$((PASS+1)); printf 'CHECK ok   - %s\n' "$1"
  else FAIL=$((FAIL+1)); printf 'CHECK FAIL - %s\n' "$1"; fi
}
conv() {  # <command> <json> ; prints stdout+stderr, returns exit code
  local cmd=$1 json=$2 out err rc
  printf '$ FM_HOME=$FM_HOME bin/fm-inbox.sh conversation %s <<< %s\n' "$cmd" "'$json'"
  printf '%s' "$json" > "$TMP/in"
  "$CLI" conversation "$cmd" < "$TMP/in" > "$TMP/out" 2> "$TMP/err"; rc=$?
  out=$(cat "$TMP/out"); err=$(cat "$TMP/err")
  [ -z "$out" ] || printf '  stdout: %s\n' "$out"
  [ -z "$err" ] || printf '  stderr: %s\n' "$err"
  printf '  exit: %s\n' "$rc"
  LAST_OUT=$out; LAST_ERR=$err; LAST_RC=$rc
  return $rc
}
jq_() { python3 -c 'import json,sys; v=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    v = v[int(k)] if isinstance(v, list) else v[k]
print(json.dumps(v) if not isinstance(v,(str,int)) else v)' "$1"; }
queue() { printf '  wake queue:\n'; if [ -s "$STATE/.wake-queue" ]; then sed 's/^/    /' "$STATE/.wake-queue"; else printf '    (empty)\n'; fi; }
inbox() { printf '  state/inbox pending: %s\n' "$(cd "$STATE/inbox" 2>/dev/null && ls *.note 2>/dev/null | tr '\n' ' ')"; printf '  state/inbox/handled: %s\n' "$(cd "$STATE/inbox/handled" 2>/dev/null && ls *.note 2>/dev/null | tr '\n' ' ')"; }
ack_from() {  # <stderr-file> [cutoff-override]
  local seq gen
  seq=$(sed -n 's/^WAKE_ACK_REQUIRED:.*--ack-through \([0-9][0-9]*\) --recovery-generation [A-Za-z0-9._-][A-Za-z0-9._-]*$/\1/p' "$1")
  gen=$(sed -n 's/^WAKE_ACK_REQUIRED:.*--ack-through [0-9][0-9]* --recovery-generation \([A-Za-z0-9._-][A-Za-z0-9._-]*\)$/\1/p' "$1")
  [ -z "${2:-}" ] || seq=$2
  printf '$ bin/fm-wake-drain.sh --ack-through %s --recovery-generation %s\n' "$seq" "$gen"
  drain --ack-through "$seq" --recovery-generation "$gen" > "$TMP/ack.out" 2> "$TMP/ack.err"; ACK_RC=$?
  sed 's/^/  stdout: /' "$TMP/ack.out"; sed 's/^/  stderr: /' "$TMP/ack.err"; printf '  exit: %s\n' "$ACK_RC"
}
run_drain() {
  printf '$ FM_STATE_OVERRIDE=%s bin/fm-wake-drain.sh\n' "$STATE"
  drain > "$TMP/drain.out" 2> "$TMP/drain.err"; DRAIN_RC=$?
  sed 's/^/  stdout: /' "$TMP/drain.out"; grep -E '^WAKE_ACK_REQUIRED|held|nothing was acknowledged' "$TMP/drain.err" | sed 's/^/  stderr: /'; printf '  exit: %s\n' "$DRAIN_RC"
}

hr "SETUP: pilot home, owning session = this codex process pid $$"
mkdir -p "$STATE"
printf '%s\n' "$$" > "$STATE/.lock"
printf 'session lock: %s\n' "$(cat "$STATE/.lock")"
conv pilot-init '{"publication_policy":"owner-authored-elevenlabs-v1"}'; check "pilot-init enables live publication for the lock holder" $?
conv bind '{"conversation_id":"captain-voice-2026-09-11","authenticated_principal":"captain"}'; check "bind returns a transport credential" $?
CRED=$(printf '%s' "$LAST_OUT" | jq_ credential)
CID=captain-voice-2026-09-11
T() { printf '{"conversation_id":"%s","credential":"%s"%s}' "$CID" "$CRED" "${1:-}"; }
O() { printf '{"conversation_id":"%s"%s}' "$CID" "${1:-}"; }

hr "S1: an ordinary typed note is queued first, then the bridge captures a spoken turn"
printf '$ FM_HOME=$FM_HOME bin/fm-inbox.sh note "remember to rotate the logs"\n'
"$CLI" note "remember to rotate the logs" | sed 's/^/  /'
conv capture "$(T ',"turn_id":"t1","request_id":"r1","committed_transcript":"Is the deploy on the migration branch green?","revision":1,"previous_turn_id":null,"created_at":"2026-09-11T23:00:00Z"')"
check "capture lands the turn as a saved request" "$([ "$(printf '%s' "$LAST_OUT" | jq_ state)" = saved ]; echo $?)"
inbox; queue
NOTE=$(cd "$STATE/inbox" && ls vc-*.note); VKEY="inbox:${NOTE%.note}"
check "a pending vc- note exists beside the request" "$([ -f "$STATE/inbox/$NOTE" ]; echo $?)"
check "one check wake keyed inbox:vc-* was appended, carrying IDs only" "$(grep -q "$VKEY" "$STATE/.wake-queue" && ! grep -q 'migration branch' "$STATE/.wake-queue"; echo $?)"
printf '$ sed -n '"'"'/^--$/,$p'"'"' "$FM_HOME/state/inbox/%s" | tail -n +2   (skill step 1)\n' "$NOTE"
sed -n '/^--$/,$p' "$STATE/inbox/$NOTE" | tail -n +2 | sed 's/^/  /'
check "the note body carries conversation_id, request_id and committed_transcript" "$(sed -n '/^--$/,$p' "$STATE/inbox/$NOTE" | tail -n +2 | python3 -c 'import json,sys; b=json.load(sys.stdin); assert b["conversation_id"] and b["request_id"]=="r1" and "migration branch" in b["committed_transcript"]'; echo $?)"

hr "S2: the drain presents the spoken turn FIRST under VOICE, ahead of the earlier typed note"
run_drain
VL=$(grep -n '^VOICE:' "$TMP/drain.out" | head -1 | cut -d: -f1); RL=$(awk -F '\t' -v k="$VKEY" 'NF==5 && $4==k {print NR; exit}' "$TMP/drain.out")
OL=$(grep -n '^OTHER WAKES' "$TMP/drain.out" | cut -d: -f1); TL=$(awk -F '\t' 'NF==5 && $4 ~ /^inbox:[0-9]/ {print NR; exit}' "$TMP/drain.out")
printf '  line numbers: VOICE heading=%s voice row=%s OTHER WAKES=%s typed row=%s\n' "$VL" "$RL" "$OL" "$TL"
check "VOICE heading, then the voice row, then OTHER WAKES, then the typed row" "$([ -n "$VL" ] && [ -n "$RL" ] && [ -n "$OL" ] && [ -n "$TL" ] && [ "$VL" -lt "$RL" ] && [ "$RL" -lt "$OL" ] && [ "$OL" -lt "$TL" ]; echo $?)"
VSEQ=$(awk -F '\t' -v k="$VKEY" 'NF==5 && $4==k {print $2; exit}' "$TMP/drain.out")
check "the voice row keeps its later sequence number 2 - presentation only" "$([ "$VSEQ" = 2 ]; echo $?)"
cp "$TMP/drain.err" "$TMP/drain1.err"

hr "S3: the generic inbox drain cannot retire a spoken turn"
printf '$ bin/fm-inbox.sh drain --ack %s\n' "${NOTE%.note}"
"$CLI" drain --ack "${NOTE%.note}" > "$TMP/o" 2> "$TMP/e"; rc=$?; sed 's/^/  stdout: /' "$TMP/o"; sed 's/^/  stderr: /' "$TMP/e"; printf '  exit: %s\n' "$rc"
check "drain --ack refuses a vc- id" "$([ $rc -ne 0 ] && [ -f "$STATE/inbox/$NOTE" ]; echo $?)"
printf '$ bin/fm-inbox.sh list\n'; "$CLI" list | sed 's/^/  /'
check "list shows the typed note but never the vc- note" "$("$CLI" list | grep -q 'rotate the logs' && ! "$CLI" list | grep -q 'vc-'; echo $?)"

hr "S4 (adversarial): acknowledge the whole drain WITHOUT answering - the instruction slip that lost the 2026-09-10 call"
ack_from "$TMP/drain1.err"
check "the acknowledgement reports it held 1 voice row" "$(grep -q 'held 1 voice wake row' "$TMP/ack.err"; echo $?)"
queue; inbox
check "the voice row is still queued, the typed row is gone, the note is still pending" "$(grep -q "$VKEY" "$STATE/.wake-queue" && ! grep -q 'inbox:1' "$STATE/.wake-queue" && [ -f "$STATE/inbox/$NOTE" ]; echo $?)"
say "-- next drain presents the held row again under VOICE --"
run_drain
check "the held voice row is presented again under VOICE" "$(grep -q '^VOICE:' "$TMP/drain.out" && grep -q "$VKEY" "$TMP/drain.out"; echo $?)"

hr "S9: a second spoken turn arrives; the earlier stale acknowledgement now names the held row honestly"
conv capture "$(T ',"turn_id":"t2","request_id":"r2","committed_transcript":"And is anything blocked behind it?","revision":1,"previous_turn_id":"t1","created_at":"2026-09-11T23:00:20Z"')" > /dev/null
run_drain
check "both voice rows are presented, no OTHER WAKES label when nothing else is queued" "$([ "$(grep -c 'inbox:vc-' "$TMP/drain.out")" -eq 2 ] && ! grep -q '^OTHER WAKES' "$TMP/drain.out"; echo $?)"
say "-- run the acknowledgement through row 2 only, while row 3 is the current presented wake --"
ack_from "$TMP/drain.err" 2
check "stale ack says every row at or below it is a held voice row (not 'none presented')" "$(grep -q 'every presented wake row at or below it is a held voice row' "$TMP/ack.err" && ! grep -q 'none of your presented' "$TMP/ack.err"; echo $?)"
check "and it still points at row 3 as the current wake" "$(grep -q 'the current wake is row 3' "$TMP/ack.err"; echo $?)"

hr "S5: the skill's operating sequence - accept, progress portion, final answer, bridge poll and speak"
conv accept "$(O)"; check "accept dispatches r1 with its committed transcript" "$([ "$(printf '%s' "$LAST_OUT" | jq_ input.request_id)" = r1 ] && printf '%s' "$LAST_OUT" | grep -q 'migration branch'; echo $?)"
inbox
check "acceptance moved the note to state/inbox/handled/ (skill step 1 reconcile pointer)" "$([ -f "$STATE/inbox/handled/$NOTE" ] && [ ! -f "$STATE/inbox/$NOTE" ]; echo $?)"
conv publish "$(O ',"request_id":"r1","response_id":"r1-p1","sequence":1,"kind":"progress","final":false,"destination":"elevenlabs","speech_text":"Still reading the records on that one."')"; check "progress portion published first (final false)" $?
conv publish "$(O ',"request_id":"r1","response_id":"r1-x","sequence":1,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Out of order."')"; check "a portion that does not follow the stream is refused" "$([ $LAST_RC -eq 2 ]; echo $?)"
conv publish "$(O ',"request_id":"r1","response_id":"r1-p2","sequence":2,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Yes. The migration branch is green and waiting on your review."')"; check "final answer published as sequence 2" $?
conv publish "$(O ',"request_id":"r1","response_id":"r1-p2","sequence":2,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Yes. The migration branch is green and waiting on your review."')"; check "identical republish is a safe retry" $?
conv publish "$(O ',"request_id":"r1","response_id":"r1-p2","sequence":2,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Different words."')"; check "same response_id with different text is refused" "$([ $LAST_RC -eq 2 ]; echo $?)"
conv publish "$(O ',"request_id":"r1","response_id":"r1-p3","sequence":3,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"After the close."')"; check "nothing publishes after the closing portion" "$([ $LAST_RC -eq 2 ]; echo $?)"
say "-- the bridge polls with its credential --"
conv poll "$(T)"; POLL=$LAST_OUT
check "poll shows r1 accepted and both portions waiting, in order, with no speech text" "$(printf '%s' "$POLL" | python3 -c '
import json,sys; p=json.load(sys.stdin)
assert next(r for r in p["requests"] if r["request_id"]=="r1")["state"]=="accepted"
w=[(r["response_id"],r["kind"],r["final"]) for r in p["replies"] if r["request_id"]=="r1" and r["delivery"]["state"]=="waiting"]
assert w==[("r1-p1","progress",False),("r1-p2","answer",True)], w
assert "speech_text" not in json.dumps(p)'; echo $?)"
conv deliver "$(T ',"response_id":"r1-p1","generation":"g1"')"; check "deliver claims the progress portion and returns the speech text" "$([ "$(printf '%s' "$LAST_OUT" | jq_ deliver)" = True ] && printf '%s' "$LAST_OUT" | grep -q 'Still reading'; echo $?)"
conv deliver "$(T ',"response_id":"r1-p1","generation":"g2"')"; check "a second claim yields no audio (claimed exactly once)" "$([ "$(printf '%s' "$LAST_OUT" | jq_ deliver)" = False ]; echo $?)"
conv playback "$(T ',"response_id":"r1-p1","generation":"g1","state":"completed","position_ms":2100')"; check "playback receipt recorded" $?
conv deliver "$(T ',"response_id":"r1-p2","generation":"g3"')"; check "the final answer is claimed and spoken" "$([ "$(printf '%s' "$LAST_OUT" | jq_ deliver)" = True ]; echo $?)"
say "-- keep accepting until dispatch false (skill step 2) --"
conv accept "$(O)"; check "accept dispatches r2 next" "$([ "$(printf '%s' "$LAST_OUT" | jq_ input.request_id)" = r2 ]; echo $?)"
conv publish "$(O ',"request_id":"r2","response_id":"r2-p1","sequence":1,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Nothing else is blocked behind it."')"; check "r2 answered" $?
conv accept "$(O)"; check "accept reports nothing waiting" "$([ "$(printf '%s' "$LAST_OUT" | jq_ dispatch)" = False ]; echo $?)"

hr "S6: once the requests have left saved, the ordinary acknowledgement retires the voice rows"
run_drain; ack_from "$TMP/drain.err"
queue
check "no hold reported and the queue is empty" "$(! grep -q held "$TMP/ack.err" && [ ! -s "$STATE/.wake-queue" ]; echo $?)"

hr "S7: session restart is a non-event - the new lock holder answers what the old session left saved"
conv capture "$(T ',"turn_id":"t3","request_id":"r3","committed_transcript":"Did the nightly run finish?","revision":1,"previous_turn_id":"t2","created_at":"2026-09-11T23:01:00Z"')" > /dev/null
printf 'journal owner before restart: %s ; policy enabled_by: %s\n' "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["conversations"][sys.argv[2]]["owner"])' "$STATE/voice-conversation/journal.json" "$CID")" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["enabled_by"])' "$STATE/voice-conversation/policy.json")"
OLD_OWNER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["conversations"][sys.argv[2]]["owner"])' "$STATE/voice-conversation/journal.json" "$CID")
cat > "$TMP/successor.sh" <<'SH'
set -e
printf 'successor session pid %s takes the lock\n' "$$"
echo "$$" > "$FM_HOME/state/.lock"
printf '%s' "{\"conversation_id\":\"$2\"}" | "$1" conversation accept
printf '%s' "{\"conversation_id\":\"$2\",\"request_id\":\"r3\",\"response_id\":\"r3-p1\",\"sequence\":1,\"kind\":\"answer\",\"final\":true,\"destination\":\"elevenlabs\",\"speech_text\":\"Yes, the nightly run finished clean.\"}" | "$1" conversation publish
printf '%s' '{"publication_policy":"owner-authored-elevenlabs-v1"}' | "$1" conversation pilot-init
SH
printf '$ codex successor.sh   (a fresh harness process, no policy.json touched by hand)\n'
"$TMP/codex" "$TMP/successor.sh" "$CLI" "$CID" > "$TMP/succ.out" 2> "$TMP/succ.err"; rc=$?
sed 's/^/  /' "$TMP/succ.out"; sed 's/^/  stderr: /' "$TMP/succ.err"; printf '  exit: %s\n' "$rc"
NEW_OWNER=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["conversations"][sys.argv[2]]["owner"])' "$STATE/voice-conversation/journal.json" "$CID")
ENABLED_BY=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["enabled_by"])' "$STATE/voice-conversation/policy.json")
printf 'journal owner after restart:  %s ; policy enabled_by: %s\n' "$NEW_OWNER" "$ENABLED_BY"
check "successor accepted r3, published, and re-ran pilot-init without refusal" "$([ $rc -eq 0 ] && grep -q '"request_id":"r3"' "$TMP/succ.out" && grep -q '"pilot":true' "$TMP/succ.out"; echo $?)"
check "ownership and enabled_by now name the successor, not the first session" "$([ "$NEW_OWNER" != "$OLD_OWNER" ] && [ "$ENABLED_BY" = "$NEW_OWNER" ]; echo $?)"
printf '%s\n' "$$" > "$STATE/.lock"
conv poll "$(T)"; check "the bridge, still on its old credential, sees the successor's answer waiting" "$(printf '%s' "$LAST_OUT" | grep -q '"response_id":"r3-p1"'; echo $?)"

hr "S7b (adversarial): every guard still refuses"
printf '%s\n' "1" > "$STATE/.lock"
conv accept "$(O)"; check "a caller not holding the lock is refused" "$([ $LAST_RC -ne 0 ]; echo $?)"
conv pilot-init '{"publication_policy":"owner-authored-elevenlabs-v1"}'; check "pilot-init from a non-holder is refused" "$([ $LAST_RC -ne 0 ]; echo $?)"
conv accept "$(T)"; check "a transport credential grants no owner authority to accept" "$([ $LAST_RC -ne 0 ]; echo $?)"
printf '%s\n' "$$" > "$STATE/.lock"
printf '$ FM_SUPERVISION_ACTOR=branch bin/fm-inbox.sh conversation accept\n'
printf '%s' "$(O)" | FM_SUPERVISION_ACTOR=branch "$CLI" conversation accept > "$TMP/o" 2> "$TMP/e"; rc=$?; sed 's/^/  stderr: /' "$TMP/e"; printf '  exit: %s\n' "$rc"
check "a Pi supervision branch actor is refused" "$([ $rc -ne 0 ]; echo $?)"
printf '$ env -u FM_HOME bin/fm-inbox.sh conversation accept\n'
printf '%s' "$(O)" | env -u FM_HOME "$CLI" conversation accept > "$TMP/o" 2> "$TMP/e"; rc=$?; sed 's/^/  stderr: /' "$TMP/e"; printf '  exit: %s\n' "$rc"
check "a caller without an explicit FM_HOME is refused" "$([ $rc -ne 0 ]; echo $?)"
printf '$ FM_STATE_OVERRIDE=... bin/fm-inbox.sh conversation accept\n'
printf '%s' "$(O)" | FM_STATE_OVERRIDE="$STATE" "$CLI" conversation accept > "$TMP/o" 2> "$TMP/e"; rc=$?; sed 's/^/  stderr: /' "$TMP/e"; printf '  exit: %s\n' "$rc"
check "a directory override is refused" "$([ $rc -ne 0 ]; echo $?)"
printf '$ bin/fm-inbox.sh conversation capture   (wrong credential)\n'
printf '{"conversation_id":"%s","credential":"wrong","turn_id":"tx","request_id":"rx","committed_transcript":"x","revision":1,"previous_turn_id":null,"created_at":"2026-09-11T23:02:00Z"}' "$CID" | "$CLI" conversation capture > "$TMP/o" 2> "$TMP/e"; rc=$?; sed 's/^/  stderr: /' "$TMP/e"; printf '  exit: %s\n' "$rc"
check "a wrong transport credential cannot file a turn" "$([ $rc -ne 0 ]; echo $?)"

hr "S8: reconcile the three permanently unacceptable states per references/publication.md"
say "-- (a) missing predecessor --"
conv capture "$(T ',"turn_id":"t4","request_id":"r4","committed_transcript":"So go ahead with that.","revision":1,"previous_turn_id":"t-never-captured","created_at":"2026-09-11T23:03:00Z"')" > /dev/null
conv accept "$(O)"; check "accept waits (dispatch false) on a request whose predecessor never arrived" "$([ "$(printf '%s' "$LAST_OUT" | jq_ dispatch)" = False ]; echo $?)"
conv audit "$(O)"; check "audit shows r4 saved with a previous_turn_id matching no turn" "$(printf '%s' "$LAST_OUT" | python3 -c '
import json,sys; a=json.load(sys.stdin); turns={r["turn_id"] for r in a["requests"]}
r=next(r for r in a["requests"] if r["request_id"]=="r4"); assert r["state"]=="saved" and r["previous_turn_id"] not in turns'; echo $?)"
conv publish "$(O ',"request_id":"r4","response_id":"r4-bad","sequence":1,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"x"')"; check "publish against the saved r4 is refused" "$([ $LAST_RC -eq 2 ]; echo $?)"
conv reject "$(O ',"request_id":"r4","reason":"previous turn t-never-captured was never captured"')"; check "reject r4 with its reason" $?
conv publish "$(O ',"request_id":"r4","response_id":"r4-q","sequence":1,"kind":"question","final":false,"question_binding":"q-r4","destination":"elevenlabs","speech_text":"I lost the turn before this one. Could you say it again?"')"; check "a question portion publishes against the rejected r4" $?
conv publish "$(O ',"request_id":"r4","response_id":"r4-a","sequence":2,"kind":"answer","final":true,"destination":"elevenlabs","speech_text":"Done."')"; check "an answer against a rejected request is refused" "$([ $LAST_RC -eq 2 ]; echo $?)"
inbox
check "the rejected note moved to handled/" "$([ "$(ls "$STATE/inbox"/vc-*.note 2>/dev/null | wc -l)" -eq 0 ]; echo $?)"
say "-- (b) stale question binding blocks the whole conversation until rejected --"
conv capture "$(T ',"turn_id":"t5","request_id":"r5","committed_transcript":"Rotate the logs on the staging box.","revision":1,"previous_turn_id":"t4","question_binding":"q-r4","created_at":"2026-09-11T23:04:00Z"')" > /dev/null
conv accept "$(O)"; check "the bound answer r5 is accepted and consumes the question" "$([ "$(printf '%s' "$LAST_OUT" | jq_ input.request_id)" = r5 ]; echo $?)"
conv capture "$(T ',"turn_id":"t6","request_id":"r6","committed_transcript":"Rotate the logs, I said.","revision":1,"previous_turn_id":"t5","question_binding":"q-r4","created_at":"2026-09-11T23:05:00Z"')" > /dev/null
conv capture "$(T ',"turn_id":"t7","request_id":"r7","committed_transcript":"Also, what time is the standup?","revision":1,"previous_turn_id":"t6","created_at":"2026-09-11T23:06:00Z"')" > /dev/null
conv accept "$(O)"; check "accept refuses for the whole conversation: exit 2 'stale or unknown question binding'" "$([ $LAST_RC -eq 2 ] && printf '%s' "$LAST_ERR" | grep -q 'stale or unknown question binding'; echo $?)"
conv audit "$(O)"; check "r7 sits saved behind r6" "$(printf '%s' "$LAST_OUT" | python3 -c '
import json,sys; a=json.load(sys.stdin); s={r["request_id"]:r["state"] for r in a["requests"]}; assert s["r6"]=="saved" and s["r7"]=="saved"'; echo $?)"
conv reject "$(O ',"request_id":"r6","reason":"that question was already answered"')"; check "reject r6" $?
conv publish "$(O ',"request_id":"r6","response_id":"r6-e","sequence":1,"kind":"error","final":true,"destination":"elevenlabs","speech_text":"I already have your answer on that one. Say the new instruction again."')"; check "error portion publishes against rejected r6" $?
conv accept "$(O)"; check "accept moves on to r7 once r6 is rejected" "$([ "$(printf '%s' "$LAST_OUT" | jq_ input.request_id)" = r7 ]; echo $?)"
say "-- (c) correction whose target is not accepted --"
conv capture "$(T ',"turn_id":"t8","request_id":"r8","committed_transcript":"Correction: I meant the other box.","revision":1,"previous_turn_id":"t7","correction_of":"r4","created_at":"2026-09-11T23:07:00Z"')" > /dev/null
conv accept "$(O)"; check "accept exits 2 'correction target not accepted here'" "$([ $LAST_RC -eq 2 ] && printf '%s' "$LAST_ERR" | grep -q 'correction target not accepted here'; echo $?)"
conv reject "$(O ',"request_id":"r8","reason":"correction targets a rejected request"')"; check "reject r8" $?
conv accept "$(O)"; check "accept reports nothing waiting after the reject" "$([ "$(printf '%s' "$LAST_OUT" | jq_ dispatch)" = False ]; echo $?)"
say "-- the wake drain after all that: rejected/accepted rows are retired by the ordinary acknowledgement --"
run_drain; ack_from "$TMP/drain.err"; queue
check "no voice row is held once every request left saved" "$(! grep -q held "$TMP/ack.err" && [ ! -s "$STATE/.wake-queue" ]; echo $?)"

hr "S10: skill packaging"
python3 - "$ROOT" <<'PY'
import sys, pathlib, yaml, re
root = pathlib.Path(sys.argv[1])
p = root / '.claude/skills/answer-voice-turn/SKILL.md'   # through the .claude symlink installers use
text = p.read_text()
m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
fm = yaml.safe_load(m.group(1))
print('  resolved through .claude/skills symlink:', p.resolve().relative_to(root))
print('  frontmatter:', {k: (v if k != 'description' else v[:70] + '...') for k, v in fm.items()})
assert fm['name'] == 'answer-voice-turn' and fm['user-invocable'] is False and fm['metadata']['internal'] is True
for link in re.findall(r'\]\((references/[^)]+)\)', text):
    assert (p.parent / link).is_file(), link
    print('  reference resolves:', link)
sibling = yaml.safe_load(re.match(r'^---\n(.*?)\n---\n', (root/'.agents/skills/fmx-respond/SKILL.md').read_text(), re.S).group(1))
assert set(sibling) == set(fm), (set(sibling), set(fm))
print('  same frontmatter keys as the existing fmx-respond skill:', sorted(fm))
PY
check "SKILL.md frontmatter parses, is internal/non-invocable like sibling skills, references resolve" $?
printf '$ bin/fm-inbox.sh conversation --help | head -3\n'; "$CLI" conversation --help | head -3 | sed 's/^/  /'

hr "RESULT: checks passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
