# fm-secondmate-restart.sh - a banner-only relaunch failure still names a cause

first_line is deliberately allowed to return nothing, so this surface owns
its own fallback wording. The stub emits only the OpenSSH banner and exits
non-zero.

## BEFORE (base 269f8fe bin/)
```
===================================================================
SCENARIO: remote relaunch leg dies with banner-only output -> report must name a cause
COMMAND : bin/fm-secondmate-restart.sh sm5
-------------------------- operator output -------------------------
unreached: sm5: the restart outcome is unknown: ** WARNING: connection is not using a post-quantum key exchange algorithm.
summary: 0 of 1 restarted, 0 nudged, 1 unreached
----------------------------- exit=3 ----------------------------
```

## AFTER (target 2938905 bin/)
```
===================================================================
SCENARIO: remote relaunch leg dies with banner-only output -> report must name a cause
COMMAND : bin/fm-secondmate-restart.sh sm5
-------------------------- operator output -------------------------
unreached: sm5: the restart outcome is unknown: the restart failed without a reported reason
summary: 0 of 1 restarted, 0 nudged, 1 unreached
----------------------------- exit=3 ----------------------------
```
