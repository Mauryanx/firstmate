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

# The supervised launcher: the rendered systemd --user unit must be valid and
# run this checkout's pickup for the home it names, with the opt-in it needs.
# Boot start and restart-on-failure need a live user manager (VM pass).
# shellcheck source=bin/fm-timeout-lib.sh
. "$ROOT/bin/fm-timeout-lib.sh"
SERVICE="$ROOT/bin/fm-courier-pickup-service.sh"
HOME_DIR="$TMP_ROOT/service-home"
CONFIG="$TMP_ROOT/config"
mkdir -p "$HOME_DIR" "$TMP_ROOT/courier-root"
installed=$(FM_HOME="$HOME_DIR" XDG_CONFIG_HOME="$CONFIG" "$SERVICE" install) || fail 'service install'
UNIT="$CONFIG/systemd/user/fm-courier-pickup.service"
assert_equals "$UNIT" "$installed" 'install prints the unit path'
assert_equals "$(FM_HOME="$HOME_DIR" "$SERVICE" render)" "$(cat "$UNIT")" 'install writes the rendered unit'
for line in "ExecStart=$ROOT/bin/fm-courier-pickup.py run" "Environment=FM_HOME=$HOME_DIR" \
  'Environment=FM_NOTIFY_COURIER=1' "WorkingDirectory=$HOME_DIR" 'Restart=on-failure' \
  'NoNewPrivileges=yes' 'WantedBy=default.target'; do
  grep -Fxq -- "$line" "$UNIT" || fail "unit lacks: $line"
done
! grep -Eq '^(User|Group|AmbientCapabilities|CapabilityBoundingSet|ReadWritePaths)=' "$UNIT" ||
  fail 'unit grants an identity, capability or path of its own'
if command -v systemd-analyze >/dev/null 2>&1; then
  verify=$(systemd-analyze verify "$UNIT" 2>&1) || fail "systemd-analyze rejected the unit: $verify"
  assert_not_contains "$verify" "$UNIT" 'systemd-analyze warns about the unit'
else
  echo 'skip: systemd-analyze not found; unit syntax unverified here'
fi
# Run the unit's own command with its own environment: it must reach the pickup
# with FM_HOME and the opt-in (relocated offline, it then refuses the missing
# binding under that home), not exit 3 or demand an explicit FM_HOME.
exec_line=$(sed -n 's/^ExecStart=//p' "$UNIT")
unit_env=()
while IFS= read -r assignment; do unit_env+=("$assignment"); done < <(sed -n 's/^Environment=//p' "$UNIT")
set +e
# shellcheck disable=SC2086 # ExecStart is a plain word list by construction.
out=$(cd "$HOME_DIR" && fm_run_timed 30 env -i PATH="$PATH" HOME="$TMP_ROOT" "${unit_env[@]}" \
  FM_COURIER_ROOT="$TMP_ROOT/courier-root" FM_COURIER_USER="$(id -un)" $exec_line 2>&1)
code=$?
set -e
expect_code 1 "$code" 'rendered command startup refusal'
assert_contains "$out" "fm-courier-pickup: " 'rendered command runs the pickup'
assert_contains "$out" "$HOME_DIR/state/imessage/binding.json" 'rendered command passes its FM_HOME'
for bad in relative/home "$TMP_ROOT/has space" "$TMP_ROOT/missing"; do
  mkdir -p "$TMP_ROOT/has space"
  if FM_HOME="$bad" XDG_CONFIG_HOME="$TMP_ROOT/refused" "$SERVICE" install 2>/dev/null; then
    fail "install accepted FM_HOME '$bad'"
  fi
done
assert_absent "$TMP_ROOT/refused/systemd/user/fm-courier-pickup.service" 'a refused install wrote a unit'
pass 'courier pickup service: valid unit runs this pickup for its home, refuses unsafe paths'
