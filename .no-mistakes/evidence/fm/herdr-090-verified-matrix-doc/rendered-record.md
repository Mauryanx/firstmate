# The reader-facing surface this change adds, as it now reads.

## docs/herdr-backend.md (new lines 5-6)

Herdr 0.9.0 is supported on narrower verified coverage than that lane.
The coordinated-upgrade record under [Herdr 0.9.0 compatibility and federation](verification/runtime-backends.md#herdr-090-compatibility-and-federation) adds schema-subset equivalence with 0.8.0, one live `blocked` edge, the presentation path, the upgrade across the captain's Mac, Hermes, and Alienware, and the Hermes-to-Alienware federation management round trip; other sections of [that record](verification/runtime-backends.md) carry the 0.9.0 evidence measured before it, so consult the file rather than this list.

## docs/verification/runtime-backends.md - new section

### Herdr 0.9.0 compatibility and federation

Herdr 0.9.0 was verified on 2026-09-18 with protocol 22 and endpoint protocol generation 1.
Each observation below names its host where the host was recorded.

The exact API-schema subset Firstmate consumes was byte-identical between 0.8.0 and 0.9.0, with SHA-256 `46491a4ab4cd2c9f9304cb020aa83263e961594a7d7732b5ceace46bca5551a0` for both versions.
That subset covers `events.subscribe`, the `pane.agent_status_changed` payload, `workspace.move`, the subscription event envelope, and the agent-status enum.
Neither the extraction command that produced those digests nor the host it ran on was recorded, so re-deriving the subset is the first step of any repeat.

The live-only event path subscribed before reading its baseline and returned a `blocked` edge in 0.230 seconds.
`fm_backend_herdr_wait_transition` produced the record, against an isolated `fm-lab-` session from `bin/fm-herdr-lab.sh`; the host was not recorded.
Herdr's stream is edge-triggered and carries no previous status, so `fm_backend_herdr_normalize_event` leaves the from-status empty and the record evidences a `blocked` edge with no baseline, not a transition out of a known prior status:

```text
rc=0
record=w1:p1<TAB>w1<TAB><empty-from><TAB>blocked<TAB>claude
elapsed=0.230s
```

[Client selection](#client-selection) owns the measured cross-version pairing rule and the `protocol_mismatch` refusal behind it.

The production presentation path on 0.9.0, on macOS, preserved the exact original focus, created one unfocused projected workspace containing only the task tab and pane, removed that exact pane during cleanup, confirmed the projected workspace absent, and preserved the same focus afterward.
The invocation behind that run was not captured either.

The coordinated live upgrade checksum-verified the 0.9.0 release artifacts on Mac arm64, Hermes x86_64, and Alienware x86_64, and each host was then observed separately:

- Captain's Mac: the checksum-verified arm64 artifact was installed at `/Users/mauryan/.local/bin/herdr`, and `/Users/mauryan/.local/bin/herdr --version` returned `herdr 0.9.0`. `herdr --remote hermes-fm --session default` then reconnected successfully without downgrading Hermes. No `status --json` verdict was captured on the Mac, so the running-client evidence there is the version string plus that reconnect.
- Hermes: `herdr status --json --session default` reported protocol 22, endpoint generation 1, `compatible=true`, and `endpoint_compatible=true` with its 0.9.0 client.
- Alienware: `ssh alienware-fm ~/.local/bin/herdr status --json --session fm-remote` reported the same four values with its 0.9.0 client.

`herdr machine list --json` on Hermes showed Alienware enabled as a saved machine, and the liveness reads below found its second mate alive.

Management across the federation boundary was exercised on 0.9.0 through the command paths below, not through the window's TUI, which was not driven in this round:

- `FM_HOME=<hermes primary home> bin/fm-send.sh alienware-heavy <instruction>` routed an instruction from the Hermes primary to the Alienware second mate running in the federated 0.9.0 `fm-remote` server, and its reply arrived on the parent channel. This proves the full round trip - dispatch into a remote 0.9.0 pane, the agent's turn, and the reply landing back in the primary's channel - which is the delivery half of managing an Alienware worker from Hermes.
- `bin/fm-on.sh alienware-heavy fm-remote-secondmate-control.sh state alienware-heavy` and `bin/fm-crew-state.sh alienware-heavy` returned their liveness verdicts, and `bin/fm-on.sh alienware-heavy fm-remote-doctor.sh` returned its readiness verdict. This proves the primary can observe a remote worker's liveness and readiness over the 0.9.0 server, which is what every management decision is gated on.
- `bin/fm-on.sh alienware-heavy fm-remote-secondmate-control.sh update alienware-heavy` completed the guarded tracked-file update, and the second mate restarted afterward. This proves a state-changing operation reaches the remote worker and that the worker comes back on 0.9.0 afterward.
