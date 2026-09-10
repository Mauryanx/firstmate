# fm-bootstrap.sh - a silent remote leg still names a cause on both convergence reports

The remote leg is killed before writing any diagnostic of its own, so ssh
returns 255 with nothing but OpenSSH's banner. Before this change both lines
printed the banner; the accepted remedy makes each surface name its own cause.

## BEFORE (base 269f8fe bin/)
```
===================================================================
SCENARIO: remote leg killed before it could speak -> both convergence reports must name a cause
COMMAND : bin/fm-bootstrap.sh   (FM_SSH_BIN = stub: banner on stderr, exit 255)
-------------------------- operator output -------------------------
SECONDMATE_SYNC: secondmate sm: skipped: remote tracked-file sync failed on host-sm: ** WARNING: connection is not using a post-quantum key exchange algorithm.
SECONDMATE_SYNC: secondmate sm: skipped: remote inheritance failed on host-sm: ** WARNING: connection is not using a post-quantum key exchange algorithm.
-------------------------------------------------------------------
```

## AFTER (target 2938905 bin/)
```
===================================================================
SCENARIO: remote leg killed before it could speak -> both convergence reports must name a cause
COMMAND : bin/fm-bootstrap.sh   (FM_SSH_BIN = stub: banner on stderr, exit 255)
-------------------------- operator output -------------------------
SECONDMATE_SYNC: secondmate sm: skipped: remote tracked-file sync failed on host-sm: the remote sync failed without a reported reason
SECONDMATE_SYNC: secondmate sm: skipped: remote inheritance failed on host-sm: the inheritance push failed without a reported reason
-------------------------------------------------------------------
```
