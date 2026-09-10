# Targeted suite run: bin/fm-test-run.sh on the five changed suites

$ bin/fm-test-run.sh tests/fm-update.test.sh tests/fm-secondmate-restart.test.sh \
    tests/fm-secondmate-sync.test.sh tests/fm-fleet-sync.test.sh tests/fm-test-run.test.sh

New/changed cases this branch adds:
  ok - the shared fast-forward library selects every family that pins its reports
  ok - failure reports ignore OpenSSH banners and select the real diagnostic
  ok - R8b a silent remote leg still names a cause on both convergence reports
  ok - T7b remote restart reports ignore leading OpenSSH banners on failure and success
  ok - T3f remote update reports ignore leading OpenSSH banners on failure and success
  ok - an SSH fetch failure reports the remote's error, not OpenSSH's banner

Pre-existing environment failure, identical at base 269f8fe (this host has no ruby):
  not ok - ruby is required to parse .github/workflows/ci.yml as YAML

FM_TEST_SUMMARY total=5 failed=1 skipped_gate=0 duration_ms=475585
FM_TEST_SUMMARY_FAMILY family=pure-contract-unit count=1 duration_ms=308976 failed=1
FM_TEST_SUMMARY_FAMILY family=secondmate count=2 duration_ms=208710 failed=0
FM_TEST_SUMMARY_FAMILY family=session-bootstrap count=2 duration_ms=67094 failed=0
