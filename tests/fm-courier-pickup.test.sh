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
SERVICE="$ROOT/bin/fm-courier-pickup-service.sh"
HOME_DIR="$TMP_ROOT/service-home"
CONFIG="$TMP_ROOT/config"
mkdir -p "$HOME_DIR" "$TMP_ROOT/courier-root"
installed=$(FM_HOME="$HOME_DIR" XDG_CONFIG_HOME="$CONFIG" "$SERVICE" install) || fail 'service install'
UNIT="$CONFIG/systemd/user/fm-courier-pickup.service"
assert_equals "$UNIT" "$installed" 'install prints the unit path'
assert_equals "$(FM_HOME="$HOME_DIR" "$SERVICE" render)" "$(cat "$UNIT")" 'install writes the rendered unit'
if command -v systemd-analyze >/dev/null 2>&1; then
  verify=$(systemd-analyze verify "$UNIT" 2>&1) || fail "systemd-analyze rejected the unit: $verify"
  assert_not_contains "$verify" "$UNIT" 'systemd-analyze warns about the unit'
else
  echo 'skip: systemd-analyze not found; unit syntax unverified here'
fi
# Run the unit's own command with its own environment: it must reach the pickup
# with FM_HOME and the opt-in (relocated offline, it then refuses the missing
# binding under that home), not exit 3 or demand an explicit FM_HOME.
python3 - "$UNIT" "$ROOT" "$HOME_DIR" "$TMP_ROOT" <<'PY' || fail 'installed unit launcher contract'
import codecs
import os
from pathlib import Path
import pwd
import shlex
import subprocess
import sys

unit, root, home, temp = sys.argv[1:]
settings = {}
section, pending = '', ''
for line in Path(unit).read_text().splitlines():
    line = line.strip()
    if not line or line.startswith(('#', ';')):
        continue
    pending += line
    if pending.endswith('\\'):
        pending = pending[:-1] + ' '
        continue
    line, pending = pending, ''
    if line.startswith('[') and line.endswith(']'):
        section = line[1:-1]
    else:
        key, value = line.split('=', 1)
        settings.setdefault((section, key.strip()), []).append(value.strip())
assert not pending, 'unfinished unit continuation'


def single(key, default=''):
    return settings.get(('Service', key), [default])[-1]


def words(value):
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = lexer.escape = ''
    return [codecs.decode(item.replace(r'\s', ' '), 'unicode_escape') for item in lexer]


def assignments(section, key):
    result = []
    for value in settings.get((section, key), []):
        if not value:
            result.clear()
        else:
            result.append(words(value))
    return result


def capabilities(key, default_inverted):
    inverted, names = default_inverted, set()
    for index, value in enumerate(settings.get(('Service', key), [])):
        negate = value.startswith('~')
        members = set(words(value[1:] if negate else value))
        if index == 0 or not members:
            inverted, names = negate, members
        elif negate:
            names = names | members if inverted else names - members
        else:
            names = names - members if inverted else names | members
    return inverted, names


command = assignments('Service', 'ExecStart')
unit_env = dict(item.split('=', 1) for line in assignments('Service', 'Environment') for item in line)
assert command == [[str(Path(root) / 'bin/fm-courier-pickup.py'), 'run']], ('ExecStart', command)
assert unit_env == {'FM_HOME': home, 'FM_NOTIFY_COURIER': '1'}, ('Environment', unit_env)
assert single('WorkingDirectory') == home, ('WorkingDirectory', single('WorkingDirectory'))
assert single('Restart') == 'on-failure', ('Restart', single('Restart'))
assert single('NoNewPrivileges').lower() in ('yes', 'true', 'on', '1'), 'NoNewPrivileges must be true'
wanted_by = {item for line in assignments('Install', 'WantedBy') for item in line}
assert wanted_by == {'default.target'}, ('WantedBy', wanted_by)
for key in ('User', 'Group'):
    assert not single(key), (key, single(key))
assert capabilities('AmbientCapabilities', False) == (False, set()), 'unit adds ambient capabilities'
assert capabilities('CapabilityBoundingSet', True) == (True, set()), 'unit changes the capability bounding set'
assert not assignments('Service', 'ReadWritePaths'), 'unit grants writable paths'

env = dict(PATH=os.environ['PATH'], HOME=temp, FM_COURIER_ROOT=str(Path(temp) / 'courier-root'),
           FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name, **unit_env)
result = subprocess.run(command[0], cwd=single('WorkingDirectory'), env=env,
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
assert result.returncode == 1, ('rendered command startup refusal', result)
assert 'fm-courier-pickup: ' in result.stdout, ('rendered command runs the pickup', result.stdout)
assert str(Path(home) / 'state/imessage/binding.json') in result.stdout, ('rendered command passes FM_HOME', result.stdout)
PY
for bad in relative/home "$TMP_ROOT/has space" "$TMP_ROOT/missing"; do
  mkdir -p "$TMP_ROOT/has space"
  if FM_HOME="$bad" XDG_CONFIG_HOME="$TMP_ROOT/refused" "$SERVICE" install 2>/dev/null; then
    fail "install accepted FM_HOME '$bad'"
  fi
done
assert_absent "$TMP_ROOT/refused/systemd/user/fm-courier-pickup.service" 'a refused install wrote a unit'
pass 'courier pickup service: valid unit runs this pickup for its home, refuses unsafe paths'
