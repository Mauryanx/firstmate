#!/usr/bin/env bash
# Contract: parsed .no-mistakes.yaml must leave commands.test absent or empty
# and must carry a non-empty test.instructions runbook.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NM="$ROOT/.no-mistakes.yaml"

test_nm_has_no_deterministic_test_command() {
  local json val
  json=$(fm_yaml_to_json "$NM") \
    || fail "could not parse .no-mistakes.yaml as YAML (needs python3 with PyYAML, or ruby with psych)"
  val=$(printf '%s' "$json" | python3 -c '
import json, sys

doc = json.load(sys.stdin) or {}
commands = doc.get("commands")
val = commands.get("test") if isinstance(commands, dict) else None
empty = val is None or val is False or (isinstance(val, str) and not val.strip())
print("" if empty else repr(val))
') || fail "failed to read commands.test from the parsed .no-mistakes.yaml"
  if [ -n "$val" ]; then
    fail "commands.test must be absent or empty so Test stays intent-targeted; got: $val"
  fi
  pass "no-mistakes does not configure commands.test"
}

test_nm_carries_a_test_instructions_runbook() {
  local json status
  json=$(fm_yaml_to_json "$NM") \
    || fail "could not parse .no-mistakes.yaml as YAML (needs python3 with PyYAML, or ruby with psych)"
  status=$(printf '%s' "$json" | python3 -c '
import json, sys

doc = json.load(sys.stdin) or {}
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
') || fail "failed to read test.instructions from the parsed .no-mistakes.yaml"
  if [ "$status" != ok ]; then
    fail "test.instructions must stay present and non-empty so the test analyzer keeps its live-evidence runbook; $status"
  fi
  pass "no-mistakes carries a non-empty test.instructions runbook"
}

test_nm_has_no_deterministic_test_command
test_nm_carries_a_test_instructions_runbook
