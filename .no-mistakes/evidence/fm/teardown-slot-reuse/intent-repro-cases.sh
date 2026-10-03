# Scratch reproduction of the 2026-10-02 incident, appended to a temp copy of
# tests/fm-teardown-endpoint-safety.test.sh (its helpers, minus its run list).
# Drives the real bin/fm-teardown.sh, real tmux on a dedicated socket, fake
# Treehouse return. Records model the incident: stale distill-scale and current
# owner wispr-register-fix both name pool slot 1, neither carries a receipt,
# and the slot claim names wispr-register-fix.
STALE=distill-scale; OWNER=wispr-register-fix
intent_setup() {  # <name> <claim:owner|absent>
  local dir; dir=$(make_case "$1")
  mark_case_as_treehouse_pool "$dir"
  git -C "$dir/project" update-ref refs/remotes/origin/main HEAD
  [ "$2" = absent ] || claim_pool_slot "$dir" "$OWNER"
  ( cd "$dir" && env -u TMUX -u TMUX_PANE "$REAL_TMUX" -S intent.sock new-session -d -s fleet -n control )
  for t in $STALE $OWNER; do
    ( cd "$dir" && env -u TMUX -u TMUX_PANE "$REAL_TMUX" -S intent.sock new-window -d -t "=fleet:" -n "fm-$t" )
    fm_write_meta "$dir/home/state/$t.meta" "window=fleet:fm-$t" "endpoint_task_id=$t" \
      "worktree=$dir/worktree" "project=$dir/project" "kind=ship" "mode=local-only"
  done
  write_close_failing_tmux_shim "$dir" intent.sock "$REAL_TMUX"
  printf '%s\n' "$dir"
}
td() {  # <dir> <id> [--force]
  local dir=$1 id=$2; shift 2
  env -u TMUX -u TMUX_PANE FM_HOME="$dir/home" FM_ROOT_OVERRIDE="$ROOT" \
    FM_RUNTIME_LOG="$dir/runtime.log" PATH="$dir/fakebin:$PATH" "$TEARDOWN" "$id" "$@"
}
windows() { ( cd "$1" && "$REAL_TMUX" -S intent.sock list-windows -a -F '#W' 2>/dev/null | tr '\n' ' ' ); }

test_intent_incident_records_retire() {
  local dir rc
  dir=$(intent_setup incident owner)
  : > "$dir/runtime.log"
  echo "== incident: teardown $STALE (stale) without --force =="
  set +e; td "$dir" $STALE > "$dir/a.out" 2> "$dir/a.err"; rc=$?; set -e
  echo "rc=$rc"; sed 's/^/  stdout: /' "$dir/a.out"; sed 's/^/  stderr: /' "$dir/a.err"
  echo "  windows after: $(windows "$dir")"
  echo "  claim after: $(tr '\n' ' ' < "$dir/pool/1/.fm-slot-owner" 2>/dev/null)"
  echo "  treehouse returns: $(grep -c 'treehouse <return>' "$dir/runtime.log")"
  [ "$rc" = 0 ] || fail "stale record $STALE could not retire"
  assert_absent "$dir/home/state/$STALE.meta" "stale record kept"
  assert_present "$dir/home/state/$OWNER.meta" "owner record removed"
  assert_present "$dir/worktree/sentinel" "successor copy touched"
  assert_grep "task=$OWNER" "$dir/pool/1/.fm-slot-owner" "owner claim lost"
  assert_no_grep 'treehouse <return>' "$dir/runtime.log" "stale teardown returned the owner's slot"
  rm -f "$dir/worktree/sentinel"; : > "$dir/runtime.log"
  echo "== incident: teardown $OWNER (current owner, merged) without --force =="
  set +e; td "$dir" $OWNER > "$dir/b.out" 2> "$dir/b.err"; rc=$?; set -e
  echo "rc=$rc"; sed 's/^/  stdout: /' "$dir/b.out"; sed 's/^/  stderr: /' "$dir/b.err"
  echo "  windows after: $(windows "$dir")"
  echo "  claim file present: $([ -e "$dir/pool/1/.fm-slot-owner" ] && echo yes || echo no)"
  echo "  treehouse returns: $(grep -c 'treehouse <return>' "$dir/runtime.log")"
  [ "$rc" = 0 ] || fail "current owner $OWNER could not retire"
  assert_absent "$dir/home/state/$OWNER.meta" "owner record kept"
  assert_grep 'treehouse <return>' "$dir/runtime.log" "owner slot not returned"
  ( cd "$dir" && "$REAL_TMUX" -S intent.sock kill-server 2>/dev/null ) || true
  pass "intent: both incident records retire without --force; stale leaves successor slot untouched, owner returns it"
}

test_intent_owner_first_order() {
  local dir rc
  dir=$(intent_setup owner-first owner)
  rm -f "$dir/worktree/sentinel"; : > "$dir/runtime.log"
  echo "== owner-first: teardown $OWNER while stale $STALE still names the slot =="
  set +e; td "$dir" $OWNER > "$dir/b.out" 2> "$dir/b.err"; rc=$?; set -e
  echo "rc=$rc"; sed 's/^/  stdout: /' "$dir/b.out"; sed 's/^/  stderr: /' "$dir/b.err"
  [ "$rc" = 0 ] || fail "stale record vetoed the current owner's teardown"
  assert_grep 'treehouse <return>' "$dir/runtime.log" "owner slot not returned"
  ( cd "$dir" && "$REAL_TMUX" -S intent.sock kill-server 2>/dev/null ) || true
  pass "intent: a stale receiptless record no longer vetoes the current owner's teardown"
}

test_intent_guards_hold() {
  local dir rc f
  for f in "" --force; do
    dir=$(intent_setup "unproven${f}" absent)
    : > "$dir/runtime.log"
    echo "== guard: no claim, both records, teardown $STALE ${f:-(no --force)} =="
    set +e; td "$dir" $STALE $f > "$dir/a.out" 2> "$dir/a.err"; rc=$?; set -e
    echo "rc=$rc"; sed 's/^/  stderr: /' "$dir/a.err"
    [ "$rc" != 0 ] || fail "unproven duplicate accepted ${f}"
    assert_present "$dir/home/state/$STALE.meta" "record removed on refusal"
    assert_present "$dir/worktree/sentinel" "copy touched on refusal"
    assert_no_grep 'treehouse <return>' "$dir/runtime.log" "slot returned on refusal"
    ( cd "$dir" && "$REAL_TMUX" -S intent.sock kill-server 2>/dev/null ) || true
  done
  dir=$(intent_setup forced-stale owner)
  : > "$dir/runtime.log"
  echo "== guard: claim names $OWNER, teardown $STALE --force =="
  set +e; td "$dir" $STALE --force > "$dir/a.out" 2> "$dir/a.err"; rc=$?; set -e
  echo "rc=$rc"; sed 's/^/  stdout: /' "$dir/a.out"; sed 's/^/  stderr: /' "$dir/a.err"
  assert_present "$dir/worktree/sentinel" "--force touched successor copy"
  assert_grep "task=$OWNER" "$dir/pool/1/.fm-slot-owner" "--force dropped successor claim"
  assert_no_grep 'treehouse <return>' "$dir/runtime.log" "--force returned successor slot"
  ( cd "$dir" && "$REAL_TMUX" -S intent.sock kill-server 2>/dev/null ) || true
  pass "intent: unproven duplicates still refuse even with --force, and --force on a stale record never touches the successor"
}

test_intent_incident_records_retire
test_intent_owner_first_order
test_intent_guards_hold
