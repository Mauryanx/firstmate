# fm-fleet-sync.sh - a failed fetch over SSH names the remote's refusal, not the banner

fm-fleet-sync previously carried its own private copy of the first-line
selector; this change points it at the shared one in bin/fm-ff-lib.sh.
The stub below is a real GIT_SSH_COMMAND, so git itself folds OpenSSH's
stderr banner into the fetch output the report reads.

## BEFORE (base 269f8fe bin/)
```
===================================================================
SCENARIO: fleet sync fetch over SSH is refused -> report must name the refusal
COMMAND : bin/fm-fleet-sync.sh   (GIT_SSH_COMMAND = stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
bannerfetch: skipped: fetch failed: ** WARNING: connection is not using a post-quantum key exchange algorithm.
----------------------------- exit=0 ----------------------------
```

## AFTER (target 2938905 bin/)
```
===================================================================
SCENARIO: fleet sync fetch over SSH is refused -> report must name the refusal
COMMAND : bin/fm-fleet-sync.sh   (GIT_SSH_COMMAND = stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
bannerfetch: skipped: fetch failed: fixture-host: Permission denied (publickey).
----------------------------- exit=0 ----------------------------
```
