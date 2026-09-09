#!/usr/bin/env bash
# Manual end-to-end drive of the Codex ACCOUNT axis on bin/fm-control.sh relaunch.
# Reuses the relaunch suite's lifecycle-modelling tmux stub (a live agent that
# really stops when the harness exit command is typed, and really relaunches
# when a launch line is typed), then drives the REAL fm-control.sh.
set -u
ROOT_REPO=${1:?repo root}
cd "$ROOT_REPO" || exit 1
HELPERS=$(mktemp "${TMPDIR:-/tmp}/relaunch-helpers.XXXXXX.sh")
sed -n '1,1725p' "$ROOT_REPO/tests/fm-control-relaunch.test.sh" \
  | sed "s|\$(dirname \"\${BASH_SOURCE\[0\]}\")/lib.sh|$ROOT_REPO/tests/lib.sh|" > "$HELPERS"
# shellcheck source=/dev/null
. "$HELPERS"

hr() { printf '\n================ %s ================\n' "$1"; }
show_launch() { printf -- '--- literal command typed into the replacement pane ---\n'; sed 's/\(CODEX_HOME=[^ ]*\).*/\1 <...launch...>/' "$1/fake/literal"; }
alive() { printf 'running agent in the pane: %s\n' "$(cat "$1/fake/command")"; }

hr "1. a same-harness codex relaunch keeps the account the task was running on"
dir=$(new_case drivecarry d50); acct="$dir/accounts/.codex-3"
make_codex_account "$acct"; add_codex_task "$dir" d50 "$acct"
printf '$ fm-control.sh d50 relaunch --note "picking the work back up"\n'
run_control "$dir" d50 relaunch --note "picking the work back up"; printf 'exit=%s\n' "$?"
printf 'recorded codex_home= : %s\n' "$(meta_field "$dir" d50 codex_home)"
show_launch "$dir"

hr "2. an explicit --codex-home moves the task onto another account"
dir=$(new_case driveswitch d51)
a1="$dir/accounts/.codex-1"; a2="$dir/accounts/.codex-5"
make_codex_account "$a1"; make_codex_account "$a2"; add_codex_task "$dir" d51 "$a1"
printf 'before: codex_home=%s\n' "$(meta_field "$dir" d51 codex_home)"
printf '$ fm-control.sh d51 relaunch --codex-home %s --note "rebalancing onto a fuller account"\n' "$a2"
run_control "$dir" d51 relaunch --codex-home "$a2" --note "rebalancing onto a fuller account"; printf 'exit=%s\n' "$?"
printf 'after : codex_home=%s  (codex_home= lines in the record: %s)\n' \
  "$(meta_field "$dir" d51 codex_home)" "$(grep -c '^codex_home=' "$dir/home/state/d51.meta")"
show_launch "$dir"

hr "3. switching harness off codex drops the account instead of carrying it"
dir=$(new_case drivedrop d53); acct="$dir/accounts/.codex-1"
make_codex_account "$acct"; add_codex_task "$dir" d53 "$acct"; printf 'claude' > "$dir/fake/becomes"
printf '$ fm-control.sh d53 relaunch --harness claude --note "leaving codex"\n'
run_control "$dir" d53 relaunch --harness claude --note "leaving codex"; printf 'exit=%s\n' "$?"
printf 'recorded codex_home= : "%s"\n' "$(meta_field "$dir" d53 codex_home)"
grep -q 'CODEX_HOME=' "$dir/fake/literal" && echo "CODEX_HOME LEAKED into the claude launch" || echo "no CODEX_HOME in the claude replacement launch"

hr "4. ADVERSARIAL: the carried account was signed out since the task started"
dir=$(new_case drivesignedout d55); acct="$dir/accounts/.codex-1"
mkdir -p "$acct"   # directory exists, but the account was signed out (no auth.json)
add_codex_task "$dir" d55 "$acct"
before=$(cat "$dir/home/state/d55.meta")
printf '$ fm-control.sh d55 relaunch --note "resuming"\n'
run_control "$dir" d55 relaunch --note "resuming"; printf 'exit=%s\n' "$?"
alive "$dir"
[ "$before" = "$(cat "$dir/home/state/d55.meta")" ] && echo "task record byte-identical" || echo "TASK RECORD MUTATED"
[ -e "$dir/home/state/d55.control-relaunch" ] && echo "JOURNAL WRITTEN" || echo "no relaunch journal was opened"

hr "5. ADVERSARIAL: --codex-home aimed at a claude task"
dir=$(new_case driveforeign d54); add_ship_task "$dir" d54 claude
acct="$dir/accounts/.codex-1"; make_codex_account "$acct"
before=$(cat "$dir/home/state/d54.meta")
printf '$ fm-control.sh d54 relaunch --codex-home %s --note "wrong axis"\n' "$acct"
run_control "$dir" d54 relaunch --codex-home "$acct" --note "wrong axis"; printf 'exit=%s\n' "$?"
alive "$dir"
[ "$before" = "$(cat "$dir/home/state/d54.meta")" ] && echo "task record byte-identical" || echo "TASK RECORD MUTATED"

hr "6. ADVERSARIAL: --codex-home aimed at a codex SECONDMATE (would strand it)"
dir=$(new_case drivesm sm9); home="$dir/home"
mkdir -p "$home/config" "$home/data/sm9"
printf 'codex\n' > "$home/config/secondmate-harness"
printf '# secondmate brief\n' > "$home/data/sm9/brief.md"
fm_git_worktree "$dir/proj" "$dir/smhome" sm-branch
mkdir -p "$dir/smhome/state" "$dir/smhome/data" "$dir/smhome/bin"
printf 'sm9\n' > "$dir/smhome/.fm-secondmate-home"
printf '# agents\n' > "$dir/smhome/AGENTS.md"
{ echo "window=fmses:fm-sm9"; echo "endpoint_task_id=sm9"; echo "worktree=$dir/smhome"
  echo "project=$dir/smhome"; echo "harness=codex"; echo "kind=secondmate"
  echo "mode=secondmate"; echo "yolo=off"; echo "model=default"; echo "effort=default"
  echo "home=$dir/smhome"; } > "$home/state/sm9.meta"
printf '%s\n' "fm-sm9" > "$dir/fake/windows"
printf '%s' "$dir/smhome" > "$dir/fake/cwd"
printf 'codex' > "$dir/fake/command"; printf 'codex' > "$dir/fake/becomes"
acct="$dir/accounts/.codex-1"; make_codex_account "$acct"
before=$(cat "$home/state/sm9.meta")
printf '$ fm-control.sh sm9 relaunch --codex-home %s\n' "$acct"
run_control "$dir" sm9 relaunch --codex-home "$acct"; printf 'exit=%s\n' "$?"
alive "$dir"
[ "$before" = "$(cat "$home/state/sm9.meta")" ] && echo "secondmate record byte-identical" || echo "RECORD MUTATED"
[ -e "$home/state/sm9.control-relaunch" ] && echo "JOURNAL WRITTEN" || echo "no relaunch journal was opened"

hr "7. --codex-home on a verb other than relaunch"
printf '$ fm-control.sh d50 exit --codex-home %s\n' "$acct"
run_control "$dir" sm9 exit --codex-home "$acct"; printf 'exit=%s\n' "$?"

hr "done"
rm -f "$HELPERS"
