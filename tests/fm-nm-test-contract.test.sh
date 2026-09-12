#!/usr/bin/env bash
# Contract: parsed .no-mistakes.yaml must leave commands.test absent or empty
# and must carry a non-empty test.instructions runbook.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NM="$ROOT/.no-mistakes.yaml"

test_nm_has_no_deterministic_test_command() {
  python3 -c 'import yaml' >/dev/null 2>&1 \
    || fail "python3 with PyYAML is required to parse .no-mistakes.yaml for this contract"
  local val
  val=$(python3 - "$NM" <<'PY'
import sys, yaml

doc = yaml.safe_load(open(sys.argv[1])) or {}
commands = doc.get("commands")
val = commands.get("test") if isinstance(commands, dict) else None
empty = val is None or val is False or (isinstance(val, str) and not val.strip())
print("" if empty else repr(val))
PY
  ) || fail "failed to parse .no-mistakes.yaml as YAML"
  if [ -n "$val" ]; then
    fail "commands.test must be absent or empty so Test stays intent-targeted; got: $val"
  fi
  pass "no-mistakes does not configure commands.test"
}

test_nm_carries_a_test_instructions_runbook() {
  python3 -c 'import yaml' >/dev/null 2>&1 \
    || fail "python3 with PyYAML is required to parse .no-mistakes.yaml for this contract"
  local status
  status=$(python3 - "$NM" <<'PY'
import sys, yaml

doc = yaml.safe_load(open(sys.argv[1])) or {}
test = doc.get("test")
if not isinstance(test, dict):
    print("test is not a mapping")
else:
    val = test.get("instructions")
    if not isinstance(val, str):
        print("test.instructions is %s rather than a string" % type(val).__name__)
    elif not val.strip():
        print("test.instructions is empty")
    else:
        print("ok")
PY
  ) || fail "failed to parse .no-mistakes.yaml as YAML"
  if [ "$status" != ok ]; then
    fail "test.instructions must stay present and non-empty so the test analyzer keeps its live-evidence runbook; $status"
  fi
  pass "no-mistakes carries a non-empty test.instructions runbook"
}

test_nm_has_no_deterministic_test_command
test_nm_carries_a_test_instructions_runbook
