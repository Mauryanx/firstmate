# bin/fm-test-run.sh - editing the shared selector now selects the suites that pin its reports

Scratch repository, one-line edit to bin/fm-ff-lib.sh and nothing else.
fm-secondmate-safety.test.sh stands for the 'secondmate' family and
fm-session-start.test.sh for 'session-bootstrap' - the families that hold
fm-secondmate-sync/restart and fm-update/fleet-sync.

## BEFORE (base 269f8fe runner)
```
git status:  M bin/fm-ff-lib.sh
$ bin/fm-test-run.sh --list --changed --base HEAD
  tests/fm-ask-user-authority.test.sh
  tests/fm-brief.test.sh
  tests/fm-cd-pretool-check.test.sh
  tests/fm-documentation-audiences.test.sh
  tests/fm-harness-adapter-references.test.sh
  tests/fm-test-isolation-proof.test.sh
  tests/fm-test-run.test.sh
```

## AFTER (target runner)
```
git status:  M bin/fm-ff-lib.sh
$ bin/fm-test-run.sh --list --changed --base HEAD
  tests/fm-ask-user-authority.test.sh
  tests/fm-brief.test.sh
  tests/fm-cd-pretool-check.test.sh
  tests/fm-documentation-audiences.test.sh
  tests/fm-harness-adapter-references.test.sh
  tests/fm-secondmate-safety.test.sh
  tests/fm-session-start.test.sh
  tests/fm-test-isolation-proof.test.sh
  tests/fm-test-run.test.sh
```
