# fm-update.sh - remote secondmate update reporting under OpenSSH's PQ banner

The ssh stub is a real FM_SSH_BIN and prepends the exact two-line OpenSSH
post-quantum warning, then crosses the real fm-on -> fm-remote-secondmate-control
boundary. Three polarities: a genuine remote failure, a success, and a remote
leg killed before it could write any diagnostic of its own (banner-only output).

## BEFORE (base 269f8fe bin/)
```
===================================================================
SCENARIO: remote secondmate home is dirty -> failure must name the real cause
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated 238a3bd..ab541e5 (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: skipped on remote-mac: ** WARNING: connection is not using a post-quantum key exchange algorithm.
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: remote secondmate home is clean -> banner must not hide success
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated 21d4630..e0012c7 (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: updated on remote-mac (e0012c759c05905b26e41c2495c2a2025504557d, instructions changed: AGENTS.md,bin,.agents/skills)
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: remote leg killed, banner-only output -> must still name a cause
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated ca6b88d..9eda91d (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: skipped on remote-mac: ** WARNING: connection is not using a post-quantum key exchange algorithm.
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

```

## AFTER (target 2938905 bin/)
```
===================================================================
SCENARIO: remote secondmate home is dirty -> failure must name the real cause
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated 366bed8..bbb03a3 (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: skipped on remote-mac: error: remote secondmate home sync skipped: dirty working tree
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: remote secondmate home is clean -> banner must not hide success
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated bfa1c9c..b731614 (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: updated on remote-mac (b73161433ab3b13a7d2994b5ff3a79ce5dec6ea0, instructions changed: AGENTS.md,bin,.agents/skills)
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: remote leg killed, banner-only output -> must still name a cause
COMMAND : bin/fm-update.sh   (FM_SSH_BIN = ssh stub emitting the OpenSSH PQ banner)
-------------------------- operator output -------------------------
firstmate: updated e33ddb9..4eb4836 (instructions changed: AGENTS.md, bin, .agents/skills)
remote secondmate sm1: skipped on remote-mac: the remote update failed without a reported reason
reread-firstmate: yes
restart-secondmates: none
nudge-secondmates: none
----------------------------- exit=0 ----------------------------

```
