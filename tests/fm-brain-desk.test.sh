#!/usr/bin/env bash
# Offline brain-room client behavior through real CLI subprocesses.
# sudo is a fixture executable; no privilege, brain or provider call occurs.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v python3 >/dev/null 2>&1 || { echo 'skip: python3 not found'; exit 0; }
TMP_ROOT=$(fm_test_tmproot fm-brain-desk)
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tests/fm-brain-desk-cases.py" "$ROOT" "$TMP_ROOT"

# The daily journal-export schedule: the rendered systemd --user timer and
# service must be valid, fire daily before the 04:05 UTC journal with missed
# runs caught up, and run this checkout's export for the home they name with
# the opt-in it needs. Firing under a live user manager is a VM pass.
SERVICE="$ROOT/bin/fm-brain-journal-export-service.sh"
HOME_DIR="$TMP_ROOT/schedule-home"
CONFIG="$TMP_ROOT/config"
mkdir -p "$HOME_DIR/data" "$TMP_ROOT/journal-input"
printf '# Backlog\n' > "$HOME_DIR/data/backlog.md"
installed=$(FM_HOME="$HOME_DIR" XDG_CONFIG_HOME="$CONFIG" "$SERVICE" install) || fail 'schedule install'
UNITS="$CONFIG/systemd/user"
assert_equals "$(printf '%s\n' "$UNITS/fm-brain-journal-export.service" "$UNITS/fm-brain-journal-export.timer")" \
  "$installed" 'install prints both unit paths'
for kind in service timer; do
  assert_equals "$(FM_HOME="$HOME_DIR" "$SERVICE" render "$kind")" "$(cat "$UNITS/fm-brain-journal-export.$kind")" \
    "install writes the rendered $kind"
done
if command -v systemd-analyze >/dev/null 2>&1; then
  verify=$(systemd-analyze verify "$UNITS/fm-brain-journal-export.service" "$UNITS/fm-brain-journal-export.timer" 2>&1) ||
    fail "systemd-analyze rejected the units: $verify"
  assert_not_contains "$verify" "$UNITS" 'systemd-analyze warns about the units'
else
  echo 'skip: systemd-analyze not found; unit syntax and calendar unverified here'
fi
python3 - "$UNITS" "$ROOT" "$HOME_DIR" "$TMP_ROOT" <<'PY' || fail 'installed schedule contract'
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

units, root, home, temp = sys.argv[1:]


def parse(path):
    settings, section = {}, ''
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        assert not line.endswith('\\'), ('unexpected continuation', line)
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1]
        else:
            key, value = line.split('=', 1)
            settings.setdefault((section, key.strip()), []).append(value.strip())
    return settings


service = parse(Path(units) / 'fm-brain-journal-export.service')
timer = parse(Path(units) / 'fm-brain-journal-export.timer')
command = [shlex.split(v) for v in service[('Service', 'ExecStart')]]
unit_env = dict(item.split('=', 1) for v in service[('Service', 'Environment')] for item in shlex.split(v))
assert command == [[str(Path(root) / 'bin/fm-brain-desk.py'), 'journal-export']], ('ExecStart', command)
assert unit_env == {'FM_HOME': home, 'FM_BRAIN_DESK_ENABLED': '1'}, ('Environment', unit_env)
assert service[('Service', 'Type')] == ['oneshot'], 'a failed export must leave the unit failed'
assert service[('Service', 'WorkingDirectory')] == [home]
assert service[('Service', 'NoNewPrivileges')][-1].lower() in ('yes', 'true', 'on', '1')
for key in ('User', 'Group', 'AmbientCapabilities', 'CapabilityBoundingSet', 'ReadWritePaths', 'Restart'):
    assert ('Service', key) not in service, ('unexpected grant or retry', key)
assert ('Install', 'WantedBy') not in service, 'only the timer is enabled'
assert timer[('Timer', 'Persistent')][-1].lower() in ('yes', 'true', 'on', '1'), 'missed runs must catch up'
assert timer[('Timer', 'Unit')] == ['fm-brain-journal-export.service']
assert timer[('Install', 'WantedBy')] == ['timers.target']
(calendar,) = timer[('Timer', 'OnCalendar')]
if shutil.which('systemd-analyze'):
    # Evaluate from a non-UTC local zone so the schedule must pin UTC itself.
    out = subprocess.run(['systemd-analyze', 'calendar', '--iterations=3', calendar], check=True, text=True,
                         stdout=subprocess.PIPE, env=dict(os.environ, TZ='Asia/Kolkata')).stdout
    times = re.findall(r'\(in UTC\): \S+ (\S+) (\d\d:\d\d:\d\d) UTC', out)
    assert len(times) == 3, ('three UTC elapses', out)
    assert all('00:00:00' <= t < '04:05:00' for _, t in times), ('fires before the 04:05 UTC journal', out)
    days = [d for d, _ in times]
    assert len(set(days)) == 3 and len({t for _, t in times}) == 1, ('once daily', out)

# Run the unit's own command with its own environment: it must export this home.
env = dict(PATH=os.environ['PATH'], HOME=temp, FM_BRAIN_JOURNAL_INPUT=str(Path(temp) / 'journal-input'), **unit_env)
result = subprocess.run(command[0], cwd=home, env=env, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=30)
assert result.returncode == 0, ('rendered export', result)
assert json.loads(result.stdout) == {'files': 1, 'copied': 1}, result.stdout
assert (Path(temp) / 'journal-input/data/backlog.md').read_text() == '# Backlog\n'
PY
for bad in relative/home "$TMP_ROOT/has space" "$TMP_ROOT/missing"; do
  mkdir -p "$TMP_ROOT/has space"
  if FM_HOME="$bad" XDG_CONFIG_HOME="$TMP_ROOT/refused" "$SERVICE" install 2>/dev/null; then
    fail "install accepted FM_HOME '$bad'"
  fi
done
assert_absent "$TMP_ROOT/refused/systemd/user" 'a refused install wrote a unit'
if "$SERVICE" render 2>/dev/null || "$SERVICE" render bogus 2>/dev/null; then
  fail 'render accepted a missing or unknown unit kind'
fi
pass 'journal export schedule: valid daily timer before 04:05 UTC runs this export for its home'
