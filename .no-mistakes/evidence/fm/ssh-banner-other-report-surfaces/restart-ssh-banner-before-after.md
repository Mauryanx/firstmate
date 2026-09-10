# fm-secondmate-restart.sh - remote restart reporting under OpenSSH's PQ banner

The ssh stub is a real FM_SSH_BIN and prepends the exact two-line OpenSSH
post-quantum warning to the remote leg's output, so the whole live path
(fm-secondmate-restart -> fm-send/fm-on -> ssh -> host-local control plane)
is exercised.

## BEFORE (base 269f8fe bin/)
```
===================================================================
SCENARIO: remote relaunch fails -> report must name the remote leg's error
COMMAND : bin/fm-secondmate-restart.sh sm3   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
unreached: sm3: the restart outcome is unknown: ** WARNING: connection is not using a post-quantum key exchange algorithm.
summary: 0 of 1 restarted, 0 nudged, 1 unreached
----------------------------- exit=3 ----------------------------

===================================================================
SCENARIO: remote relaunch succeeds under the banner -> must stay a counted restart
COMMAND : bin/fm-secondmate-restart.sh sm4   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
restarted: sm4 on remote-mac (pi)
summary: 1 of 1 restarted, 0 nudged, 0 unreached
----------------------------- exit=0 ----------------------------

```

## AFTER (target 2938905 bin/)
```
===================================================================
SCENARIO: remote relaunch fails -> report must name the remote leg's error
COMMAND : bin/fm-secondmate-restart.sh sm3   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
unreached: sm3: the restart outcome is unknown: remote restart fixture failed after persistence
summary: 0 of 1 restarted, 0 nudged, 1 unreached
----------------------------- exit=3 ----------------------------

===================================================================
SCENARIO: remote relaunch succeeds under the banner -> must stay a counted restart
COMMAND : bin/fm-secondmate-restart.sh sm4   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
restarted: sm4 on remote-mac (pi)
summary: 1 of 1 restarted, 0 nudged, 0 unreached
----------------------------- exit=0 ----------------------------

```
