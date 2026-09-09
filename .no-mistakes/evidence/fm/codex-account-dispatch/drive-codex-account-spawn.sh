#!/usr/bin/env bash
# Manual end-to-end drive of the Codex account axis on bin/fm-spawn.sh.
# Runs the REAL fm-spawn.sh against an isolated firstmate home, a real git
# worktree, and a fake tmux that records the literal command firstmate types
# into the worker pane. Prints an operator-readable transcript.
set -u
ROOT_REPO=${1:?repo root}
cd "$ROOT_REPO" || exit 1
# shellcheck source=/dev/null
. "$ROOT_REPO/tests/fixtures.sh"

TMP_ROOT=$(fm_test_tmproot codex-account-drive)
ACCOUNTS="$TMP_ROOT/accounts"
mkdir -p "$ACCOUNTS/.codex-3" "$ACCOUNTS/.codex-signed-out"
printf '%s\n' '{"placeholder":"fixture-not-a-real-credential"}' > "$ACCOUNTS/.codex-3/auth.json"

hr() { printf '\n================ %s ================\n' "$1"; }

new_case() {  # <name> <harness> <id>
  local name=$1 harness=$2 id=$3 dir
  dir="$TMP_ROOT/$name"
  CASE_HOME="$dir/home"; CASE_PROJ="$dir/project"; CASE_WT="$dir/wt"
  CASE_LOG="$dir/launch.log"
  CASE_FAKEBIN=$(fm_test_make_spawn_fakebin "$dir/fake" timeout)
  fm_test_spawn_home "$CASE_HOME" "$harness"
  fm_git_worktree "$CASE_PROJ" "$CASE_WT" "wt-$name" >/dev/null 2>&1
  fm_test_spawn_brief "$CASE_HOME" "$id"
  : > "$CASE_LOG"
}

run_it() {  # <id> [extra spawn args...]
  local id=$1; shift
  CLAUDE_CONFIG_DIR='' FM_FAKE_LAUNCH_LOG="$CASE_LOG" \
    fm_test_run_spawn "$CASE_HOME" "$CASE_WT" "$CASE_FAKEBIN" \
      "$id" "$CASE_PROJ" --mode no-mistakes --yolo off "$@"
}

hr "1. codex ship spawn ON account ~/.codex-3 (--codex-home)"
new_case acct-on codex ship-on-a1
printf '$ fm-spawn.sh ship-on-a1 <project> --mode no-mistakes --yolo off --model gpt-5 --codex-home %s\n' "$ACCOUNTS/.codex-3"
run_it ship-on-a1 --model gpt-5 --codex-home "$ACCOUNTS/.codex-3"; printf 'exit=%s\n' "$?"
printf -- '--- launch command typed into the worker pane ---\n'; cat "$CASE_LOG"
printf -- '--- state/ship-on-a1.meta (account axis lines) ---\n'
grep -E '^(harness|model|kind|codex_home)=' "$CASE_HOME/state/ship-on-a1.meta"

hr "2. codex ship spawn with NO --codex-home (default account path unchanged)"
new_case acct-off codex ship-off-a2
printf '$ fm-spawn.sh ship-off-a2 <project> --mode no-mistakes --yolo off --model gpt-5\n'
run_it ship-off-a2 --model gpt-5; printf 'exit=%s\n' "$?"
printf -- '--- launch command typed into the worker pane ---\n'; cat "$CASE_LOG"
printf -- '--- codex_home= lines in state/ship-off-a2.meta ---\n'
grep -c '^codex_home=' "$CASE_HOME/state/ship-off-a2.meta" | sed 's/^/count: /'

hr "3. ADVERSARIAL: signed-out account (directory with no auth.json)"
new_case acct-noauth codex ship-noauth-a3
printf '$ fm-spawn.sh ship-noauth-a3 <project> ... --codex-home %s\n' "$ACCOUNTS/.codex-signed-out"
run_it ship-noauth-a3 --codex-home "$ACCOUNTS/.codex-signed-out"; printf 'exit=%s\n' "$?"
printf -- '--- did it silently fall back and spawn anyway? ---\n'
if [ -e "$CASE_HOME/state/ship-noauth-a3.meta" ]; then
  echo "FALLBACK: a task record was published"
else
  echo "no task record published, no launch typed: $( [ -s "$CASE_LOG" ] && echo "LAUNCH LEAKED" || echo "launch log empty")"
fi

hr "4. ADVERSARIAL: --codex-home on a non-codex harness (claude)"
new_case acct-claude claude ship-claude-a4
printf '$ fm-spawn.sh ship-claude-a4 <project> ... --codex-home %s   (home pinned to claude)\n' "$ACCOUNTS/.codex-3"
run_it ship-claude-a4 --codex-home "$ACCOUNTS/.codex-3"; printf 'exit=%s\n' "$?"

hr "5. ADVERSARIAL: metadata injection through a control byte in the path"
new_case acct-inject codex ship-inject-a5
injected="$ACCOUNTS/"$'codex-x\nspawn_gen=forged'
mkdir -p "$injected"; printf '{"placeholder":1}\n' > "$injected/auth.json"
printf '$ fm-spawn.sh ship-inject-a5 <project> ... --codex-home "<path with an embedded newline>"\n'
run_it ship-inject-a5 --codex-home "$injected"; printf 'exit=%s\n' "$?"
[ -e "$CASE_HOME/state/ship-inject-a5.meta" ] && echo "INJECTED RECORD PUBLISHED" || echo "no task record published"

hr "6. ADVERSARIAL: --codex-home on a secondmate launch"
new_case acct-sm codex sm-a6
mkdir -p "$TMP_ROOT/acct-sm/sm-home"
printf '$ fm-spawn.sh sm-a6 <home> --secondmate --codex-home %s\n' "$ACCOUNTS/.codex-3"
CLAUDE_CONFIG_DIR='' FM_FAKE_LAUNCH_LOG="$CASE_LOG" \
  fm_test_run_spawn "$CASE_HOME" "$CASE_WT" "$CASE_FAKEBIN" \
    sm-a6 "$TMP_ROOT/acct-sm/sm-home" --secondmate --codex-home "$ACCOUNTS/.codex-3"
printf 'exit=%s\n' "$?"

hr "done"

hr "7. ADVERSARIAL: an account path holding a space, a quote, and shell metacharacters"
new_case acct-quote codex ship-quote-a7
tricky="$ACCOUNTS/my codex' \$(touch /tmp/fm-pwned); acct"
mkdir -p "$tricky"; printf '{"placeholder":1}\n' > "$tricky/auth.json"
rm -f /tmp/fm-pwned
printf '$ fm-spawn.sh ship-quote-a7 <project> ... --codex-home "%s"\n' "$tricky"
run_it ship-quote-a7 --codex-home "$tricky"; printf 'exit=%s\n' "$?"
printf -- '--- launch prefix as typed into the pane ---\n'
sed 's/ env -u.*/ env -u <...rest of launch...>/' "$CASE_LOG"
printf -- '--- recorded account ---\n'; grep '^codex_home=' "$CASE_HOME/state/ship-quote-a7.meta"
printf -- '--- did the embedded command substitution execute? ---\n'
[ -e /tmp/fm-pwned ] && echo "COMMAND SUBSTITUTION EXECUTED" || echo "no; the path is shell-quoted, nothing ran"
printf -- '--- the quoted prefix still evaluates back to the exact directory ---\n'
prefix=$(sed -n "s/^CODEX_HOME=\(.*\) env -u.*/\1/p" "$CASE_LOG")
eval "resolved=$prefix"
[ "$resolved" = "$tricky" ] && echo "round-trips to the account directory exactly" || printf 'MISMATCH: %s\n' "$resolved"
