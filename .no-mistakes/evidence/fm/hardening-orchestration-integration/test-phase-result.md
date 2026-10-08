# Test phase result

Validated source: 709210e746763513a9e7448145d4ce006f3aede0, with test-only improvements retained for the outer executor.

The targeted zone-client, conversation, wake-queue and ordinary steering executable checks passed.
The expected pre-fix conversation failure was reproduced against the archived b980adfeb247e2a3fd6698922a7ee433e5ea258a product: actual capture replaced the staged shared journal with mode 0600.
The current CLI preserves artifact modes, group and effective named-peer ACLs under its 0077 umask, across replacement and a successor process; its authorization and private-policy checks still hold.
The added regression launches actual capture and wake-drain processes, exercises abandoned queue/recovery lock reclamation, preserves saved wakes, then acknowledges accepted wakes with shared metadata intact.

Initial wake tests failed because one-second checkpoints expired before progress was observed.
The isolated counterfactual changed only those observation windows to four seconds and passed.
The test fix gives quiet observation checkpoints the same four-second window as alert checkpoints and asserts persisted progress before advancing the fixture clock.
The final complete targeted wake script passed, including serialization, generation-bound acknowledgement and interruption recovery.
No runtime code was changed in this test round.

The manual drive started the real supervision daemon with a real isolated tmux pane showing a synthetic busy sentinel, never a vendor agent.
It published one courier proposal, logged pending delivery and disabled errors, retained the durable alarm, and logged ID/digest before a deliberately delayed receipt scan exceeded its watchdog.
A synthetic immutable sent receipt then exercised the real delivery reader and recovered that identity without resubmission.
This proves Firstmate publication/receipt/caller behavior; it does not prove courier approval, provider delivery or vendor-harness rendering.
Direct brain-result CLI reads accepted safe facts and refused instruction/link payloads with empty stdout.
The runner's changed-file selector was exercised only in list mode; no broadly selected suite was executed.

Complete integration acceptance remains open.
The brainreader/courier identities, privileged desk wrapper and installed request/result endpoints are absent here.
Distinct-service-identity access, policy/session-lock protection, dedicated mounts, negative peer privacy and private PID namespace owner visibility still require the approved VM acceptance environment.
No final verified notification-adapter/courier dependency or the named pinned pre-VM evidence bundle was supplied inside this worktree or the task evidence folder.
Actual dummy-data adapter roundtrips, including owner-only polls, retry, fresh vote consumption, approval/edit/denial, ambiguous sends and caller errors, remain required against final verified source pins.
The optional courier parity case was skipped; its imported rig would not by itself certify the actual adapter.
These results do not waive the installed-zone requirement or the parked cross-project acceptance dependency.

No real agent, provider send, credential change, service activation, VM provisioning, lint/static analysis, other gate phase or pipeline-control command was performed.
Disposable homes, the archived baseline and the isolated tmux runtime were removed.
Only intentional test-file changes remain in the worktree; evidence files remain in this directory.
