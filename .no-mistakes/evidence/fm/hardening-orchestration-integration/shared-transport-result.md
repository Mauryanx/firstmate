# Shared conversation/wake test-phase evidence

Source pin: `git rev-parse HEAD` returned `b980adfeb247e2a3fd6698922a7ee433e5ea258a`; fixes are uncommitted phase changes in the assigned worktree.
No other pipeline phase, external send, sibling edit, credential/configuration/service change or VM provisioning was performed.

## Root cause and reproduction

The actual conversation CLI forces umask 0077; its atomic writer published new inodes without the reviewed group/mode/access ACL.
The real capture therefore replaced a pre-staged 0660 journal (with target-only named-peer ACL, no default ACL on its parent) with 0600.
The wake writer also created private dynamic owner directories and explicitly chmodded replacement recovery markers 0600.
`shared-conversation-before.log` reproduces capture failure using the archived target's real fm-inbox.sh and the new behavior regression (fault injection stops after durable mapping).
`shared-wake-before.log` reproduces 0700 dynamic queue owner directories using the archived target's real wake library.
Only tests were overlaid into that disposable archived tree; its product code was unchanged.

## Focused checks

- Before: `TMPDIR="$PWD/.test-phase-tmp" PYTHONDONTWRITEBYTECODE=1 FM_CONVERSATION_CASE=shared bash .test-phase-tmp/before/tests/fm-inbox-conversation.test.sh` failed: journal mode 0600 instead of 0660 after actual capture.
- Before: `TMPDIR="$PWD/.test-phase-tmp" PYTHONDONTWRITEBYTECODE=1 bash .test-phase-tmp/before/tests/fm-wake-queue.test.sh --shared-interface` failed: owner directory mode 0700 instead of 0770.
- After: `TMPDIR="$PWD/.test-phase-tmp" PYTHONDONTWRITEBYTECODE=1 FM_CONVERSATION_CASE=shared bash tests/fm-inbox-conversation.test.sh` passed (`shared-conversation-after.log`).
- After: `TMPDIR="$PWD/.test-phase-tmp" PYTHONDONTWRITEBYTECODE=1 bash tests/fm-wake-queue.test.sh --shared-interface` passed (`shared-wake-after.log`).
- Existing conversation regression plus shared case: `TMPDIR="$PWD/.test-phase-tmp" PYTHONDONTWRITEBYTECODE=1 bash tests/fm-inbox-conversation.test.sh`; passed, including all existing private/session/recovery cases and the new shared case (`conversation-private-and-shared.log`).

Shared CLI assertions cover new files, second atomic replacement, fresh-process retry, saved-turn recovery/wake, independent successor session, actual accept/publish/poll/deliver/playback, pending-to-handled rename, wrong credential, wrong owner/session and branch refusals.
Policy replacement and lab/catalog artifacts stay private; malformed over-broad templates refuse publication.
Real getfacl/setfacl are installed and exercised: named Unix peer UID is looked up from the actual nobody account, with effective ACL masks checked alongside modes/group. No pwd/getuid patching is used.
Those metadata checks do not establish actual distinct-user service isolation.
Traversal-only state refuses wake while preserving the saved turn; write+traverse at 0300 permits capture/wake while actual ls fails.
Wake cases cover new/second recovery-marker publication, generation preservation, stale-generation refusal, acknowledgement, real stale lock reclamation and queue replacement, with normal-home wake files 0600 and unrelated owner directories 0700.
The full repository suite and linters/static analysis were not run.

## Required upstream staging and remaining acceptance

The zone owner must stage the protected dedicated interface home and artifact-specific metadata templates described by `python3 bin/fm_shared_interface.py --help`, including reviewed group membership and both intended identities' ACLs; no blanket default ACL or broad operational-state exposure is authorized.
Upstream staging/services must expose only the dedicated state parent with write+traverse (no listing required), independently stage inbox/handled/conversation operations, and protect policy/session-lock names through read-only owned mounts/directories.
Only approved journal/flock, immutable vc notes, queue/sequence/recovery files and their two dynamic lock families share metadata.
No upstream source or service was changed here.

The named private pre-VM evidence files were not supplied in this worktree or the task's allowed evidence folder: candidate-4d7ca18a-pins.json, conversation-permission-probe.py/json/txt, candidate-4d7ca18a-roundtrip.txt, candidate-4d7ca18a-zones.txt, configured-cadence.json and confirmed-findings.md.
Their location was requested; no pin verification or fresh upstream roundtrip is claimed without them.
Courier candidate `4d7ca18a2c7a2358d8c26ceccc540efd62a39290` and zone owner revision `b8fe0f4edc604265431bed4c26e111a2f9d8bd24` remain user-supplied pins, not newly verified dependencies; the courier candidate is not a final accepted release and its OS CI was reported failed.
Main must recheck those dependencies and rerun affected cases after courier corrections.

Installed bounded brain-desk execution and OS-enforced five-zone privacy remain unverified VM acceptance requirements.
Also outstanding: distinct real Unix identities' positive/negative filesystem operations, inability to edit policy/session lock or read unrelated home/state/config/data/credentials/brainview, dedicated mount enforcement, and private PID namespace owner-liveness visibility.
`--unshare-pid` and host /proc exposure were not changed; owner PID visibility remains a VM hypothesis.
Actual final notification adapter/courier dummy-data roundtrips (owner-only polls, poll retry, fresh vote consumption, approval/edit/denial, ambiguous sends, confirmed provider receipts and caller errors) remain required before complete integration acceptance.
These local results neither waive test-2 nor certify installed-zone/final-adapter acceptance.

Cleanup: removed the disposable .test-phase-tmp archive/fixtures; final git status contains only the six intentional modified source/doc/test files and the new bin/fm_shared_interface.py module. No prepared dependencies were removed.
