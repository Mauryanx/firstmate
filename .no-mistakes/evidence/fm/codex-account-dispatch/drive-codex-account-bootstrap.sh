#!/usr/bin/env bash
# Manual end-to-end drive of the crew-dispatch codexHome field through the REAL
# bin/fm-bootstrap.sh, using the six-account example profile this change ships.
set -u
ROOT_REPO=${1:?repo root}
cd "$ROOT_REPO" || exit 1
# shellcheck source=/dev/null
. "$ROOT_REPO/tests/fixtures.sh"
# reuse the bootstrap suite's fake toolchain builders verbatim
eval "$(sed -n '44,141p' "$ROOT_REPO/tests/fm-bootstrap.test.sh")"

LAB=$(fm_test_tmproot codex-account-bootstrap)
hr() { printf '\n================ %s ================\n' "$1"; }

boot() {  # <case> <profile-json-file>
  local name=$1 profile=$2 home fakebin
  home="$LAB/$name/home"; mkdir -p "$home/config"
  printf '%s\n' manual > "$home/config/backlog-backend"
  cp "$profile" "$home/config/crew-dispatch.json"
  fakebin=$(make_fake_toolchain "$LAB/$name"); add_real_jq "$fakebin"
  PATH="$fakebin:$PATH" FM_HOME="$home" FM_ROOT_OVERRIDE="$home" \
    FM_BOOTSTRAP_VERBOSE_FACTS=1 FM_FAKE_TREEHOUSE_LEASE_HELP=1 \
    "$ROOT_REPO/bin/fm-bootstrap.sh" 2>&1 | grep -E 'crew dispatch|CREW_DISPATCH'
}

hr "1. the shipped six-account example profile (docs/examples/crew-dispatch.json)"
sed -n '20,30p' "$ROOT_REPO/docs/examples/crew-dispatch.json"
printf -- '\n--- what bootstrap reports to the operator ---\n'
boot six "$ROOT_REPO/docs/examples/crew-dispatch.json"

hr "2. ADVERSARIAL: codexHome on a non-codex profile"
printf '%s\n' '{"rules":[{"when":"claude work","use":{"harness":"claude","codexHome":"~/.codex-1"}}]}' > "$LAB/bad1.json"
cat "$LAB/bad1.json"; printf -- '--- bootstrap ---\n'; boot bad1 "$LAB/bad1.json"

hr "3. ADVERSARIAL: empty codexHome"
printf '%s\n' '{"default":[{"harness":"codex","codexHome":""}]}' > "$LAB/bad2.json"
cat "$LAB/bad2.json"; printf -- '--- bootstrap ---\n'; boot bad2 "$LAB/bad2.json"

hr "4. ADVERSARIAL: non-string codexHome"
printf '%s\n' '{"rules":[{"when":"codex work","use":[{"harness":"codex","codexHome":3}]}]}' > "$LAB/bad3.json"
cat "$LAB/bad3.json"; printf -- '--- bootstrap ---\n'; boot bad3 "$LAB/bad3.json"

hr "5. a mixed array where only one member carries the account axis"
printf '%s\n' '{"rules":[{"when":"mixed","use":[{"harness":"codex","codexHome":"~/.codex"},{"harness":"pi","codexHome":"~/.codex"}]}]}' > "$LAB/bad4.json"
cat "$LAB/bad4.json"; printf -- '--- bootstrap ---\n'; boot bad4 "$LAB/bad4.json"

hr "done"
