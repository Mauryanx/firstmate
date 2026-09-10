# fm-update.sh - adversarial shapes against the OpenSSH banner filter

Each drive replaces the ssh stub with one that writes a deliberately hostile
output shape, then runs the real bin/fm-update.sh. Only the remote-secondmate
report lines are shown.

```
===================================================================
SCENARIO: banner, then the specific cause, then a trailing generic wrapper error: the specific cause must win
--- remote leg (ssh stub) body ---
  printf '%s\n' \
    '** WARNING: connection is not using a post-quantum key exchange algorithm.' \
    '** This session may be vulnerable to store now, decrypt later attacks.' \
    'firstmate: skipped: dirty working tree' \
    'reread-firstmate: no' \
    'error: remote code root did not complete a safe origin update' >&2
  exit 1
----------------------------------
COMMAND : bin/fm-update.sh
-------------------------- operator output -------------------------
remote secondmate sm1: skipped on remote-mac: firstmate: skipped: dirty working tree
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: a '** ' line AFTER the command's own output began is real output, not banner
--- remote leg (ssh stub) body ---
  printf '%s\n' \
    '** WARNING: connection is not using a post-quantum key exchange algorithm.' \
    'remote home: skipped: dirty working tree' \
    '** not a banner: this line is the command speaking' >&2
  exit 1
----------------------------------
COMMAND : bin/fm-update.sh
-------------------------- operator output -------------------------
remote secondmate sm1: skipped on remote-mac: remote home: skipped: dirty working tree
----------------------------- exit=0 ----------------------------

===================================================================
SCENARIO: blank and whitespace-only lines inside the leading banner run must not end the skip
--- remote leg (ssh stub) body ---
  printf '%s\n' \
    '** WARNING: connection is not using a post-quantum key exchange algorithm.' \
    '' \
    '   ' \
    '** This session may be vulnerable to store now, decrypt later attacks.' \
    'error: remote home could not import abc123' >&2
  exit 1
----------------------------------
COMMAND : bin/fm-update.sh
-------------------------- operator output -------------------------
remote secondmate sm1: skipped on remote-mac: error: remote home could not import abc123
----------------------------- exit=0 ----------------------------

```
