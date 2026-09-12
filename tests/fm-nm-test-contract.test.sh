#!/usr/bin/env bash
# Contract: parsed .no-mistakes.yaml must leave commands.test absent or empty
# and must carry a non-empty test.instructions runbook.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NM="$ROOT/.no-mistakes.yaml"

command -v ruby >/dev/null 2>&1 \
  || fail "ruby is required to parse .no-mistakes.yaml as YAML"

test_nm_has_no_deterministic_test_command() {
  local val
  val=$(ruby -ryaml -e '
doc = YAML.load_file(ARGV[0]) || {}
cmds = doc["commands"] || {}
val = cmds.is_a?(Hash) ? cmds["test"] : nil
puts (val.nil? || val == false || val == "") ? "" : val.inspect
' "$NM") || fail "failed to parse .no-mistakes.yaml as YAML"
  if [ -n "$val" ]; then
    fail "commands.test must be absent or empty so Test stays intent-targeted; got: $val"
  fi
  pass "no-mistakes does not configure commands.test"
}

test_nm_carries_a_test_instructions_runbook() {
  local status
  status=$(ruby -ryaml -e '
doc = YAML.load_file(ARGV[0]) || {}
test = doc["test"]
if !test.is_a?(Hash)
  puts "test is not a mapping"
else
  val = test["instructions"]
  if !val.is_a?(String)
    puts "test.instructions is #{val.class} rather than a string"
  elsif val.strip.empty?
    puts "test.instructions is empty"
  else
    puts "ok"
  end
end
' "$NM") || fail "failed to parse .no-mistakes.yaml as YAML"
  if [ "$status" != ok ]; then
    fail "test.instructions must stay present and non-empty so the test analyzer keeps its live-evidence runbook; $status"
  fi
  pass "no-mistakes carries a non-empty test.instructions runbook"
}

test_nm_has_no_deterministic_test_command
test_nm_carries_a_test_instructions_runbook
