The checks run the shipped fm-inbox.sh and fm-courier-pickup.py entry points as processes against disposable FM_HOME and courier spool roots inside this worktree.
The shell named codex is the existing suite ownership fixture, not a vendor agent.
Clock steps are injected into time.time in the pickup process before executing its __main__; time.monotonic and sleep remain real.
The test never imports product functions or changes the host clock.
Courier receipt inputs model the external courier boundary; these checks establish Firstmate outbox publication and acknowledgement handling, not network delivery to an iPhone.

Acceptance oracles are the author's raw-bind activation contract and clock-rollback requirement, plus the existing CLI contract of a two-second reply cadence and five/twenty-second capture retries.
clock-bind-current-final.log shows raw-bind startup, preserved default binding shape, refused rebinds, backward/forward reply publication, retry delays, and legacy pending-state restart recovery.
clock-bind-base-final.log reproduces missing destination, backward reply/retry stalls, rushed forward retries, and stale-deadline restart failure using the two changed binaries from the base commit with the same CLI dependencies.
forward-current.log and forward-base.log additionally move the clock only after publication commits, to avoid a clock-triggered empty poll masking reply timing.
The retained clock-bind-driver.py includes that refined ordering and accepts optional scenario names after its root, temp and owner arguments.

Earlier clock-bind-current.log and clock-bind-base.log include setup attempts: the driver initially allowed its lock-owning shell to exit and created group-writable courier policy files.
The driver was corrected to retain its owner shell and use protected policy permissions; final logs supersede those setup attempts.
No tracked source or test files were changed.
