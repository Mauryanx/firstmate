# Firstmate portable test shards

`bin/fm-test-run.sh` owns portable lane composition and execution.
`bin/fm-test-isolation-proof.sh` owns the proven-isolated candidate set.

## Verification inputs

The current candidate timings retain the slowest per-script measurements from green Mauryanx/firstmate CI runs [34659860712](https://github.com/Mauryanx/firstmate/actions/runs/34659860712), [34675712148](https://github.com/Mauryanx/firstmate/actions/runs/34675712148), and [34700965460](https://github.com/Mauryanx/firstmate/actions/runs/34700965460) on 2026-09-12.
The 2026-08-20 concurrent proof recorded in [fm-test-isolation-proof.md](fm-test-isolation-proof.md) remains the isolation evidence for the same 24 candidates.

| duration_ms | script |
|---:|---|
| 280182 | `tests/fm-captain-hold-lifecycle.test.sh` |
| 163458 | `tests/fm-lint.test.sh` |
| 115967 | `tests/fm-pr-merge.test.sh` |
| 87525 | `tests/fm-test-run.test.sh` |
| 32456 | `tests/fm-arm-pretool-check.test.sh` |
| 26092 | `tests/fm-x-mode.test.sh` |
| 23678 | `tests/fm-backend-herdr.test.sh` |
| 15364 | `tests/fm-cd-pretool-check.test.sh` |
| 12174 | `tests/fm-crew-state.test.sh` |
| 7014 | `tests/fm-herdr-lab.test.sh` |
| 6315 | `tests/fm-grok-harness.test.sh` |
| 6022 | `tests/fm-pi-primary-types.test.sh` |
| 5151 | `tests/fm-send-popup-settle.test.sh` |
| 4803 | `tests/fm-composer-lib.test.sh` |
| 3969 | `tests/fm-send-strict.test.sh` |
| 2550 | `tests/fm-review-diff.test.sh` |
| 2507 | `tests/fm-tmux-submit-busy.test.sh` |
| 2386 | `tests/fm-spawn-batch.test.sh` |
| 2084 | `tests/fm-send-settle.test.sh` |
| 1953 | `tests/fm-composer-ghost.test.sh` |
| 1572 | `tests/fm-brief.test.sh` |
| 917 | `tests/fm-ensure-agents-md.test.sh` |
| 304 | `tests/fm-supervision-instructions.test.sh` |
| 101 | `tests/fm-transition-lib.test.sh` |

## Parallel lanes

The two parallel lanes use longest-processing-time assignment from those measured durations.

| Lane | Script count | Estimated duration |
|---|---:|---:|
| `portable-parallel-1` | 13 | 402071 ms (~6.70 min) |
| `portable-parallel-2` | 11 | 402473 ms (~6.71 min) |
| imbalance | | 402 ms |

`bin/fm-test-run.sh` contains the exact ordered memberships in `list_portable_parallel_1` and `list_portable_parallel_2`.

## Portable serial remainder

`portable-serial` includes every `tests/*.test.sh` that is neither proven-isolated nor `real-herdr-gated`.
It keeps watcher, lock, AFK, real tmux, daemon, secondmate lifecycle, bootstrap, the `live-harness-optin` family, GUI-backend, and other unproven work serial.
Membership is derived rather than enumerated, so a newly added test lands here by default.

## Portable serial CI shards

On green CI run [30725985757](https://github.com/kunchenguid/firstmate/actions/runs/30725985757), that remainder accumulated 19m04s of script time against a 20-minute job timeout.
On [PR 1495](https://github.com/kunchenguid/firstmate/pull/1495), its main step ran about 19m51s before the job was cancelled at that boundary.
`portable-serial-<k>of<n>` splits it across `n` separate CI runners.
Each shard is still strictly serial in itself, and separate runners mean no two of these stateful scripts ever share a machine, so the split needs no concurrency isolation proof.

`bin/fm-test-run.sh` owns `n` and refuses any lane whose `of<n>` disagrees with it.
`.github/workflows/ci.yml` derives the same `n` from `strategy.job-total` rather than a literal, so changing the shard count in either file without the other fails the lane loudly instead of leaving part of the required suite unrun.

Assignment is longest-processing-time bin packing over per-script duration hints embedded in `bin/fm-test-run.sh`.
The 160 current hints retain the slowest measurements from the prior evidence set and the `fm-test-timing-portable-serial-*` artifacts of green Mauryanx/firstmate CI runs [34659860712](https://github.com/Mauryanx/firstmate/actions/runs/34659860712), [34675712148](https://github.com/Mauryanx/firstmate/actions/runs/34675712148), and [34700965460](https://github.com/Mauryanx/firstmate/actions/runs/34700965460) on 2026-09-12.
Those per-script maxima total 5874785 ms of conservative balance weight.
Taking the slowest of several CI runs rather than a single run keeps the balance honest on a slow runner.
A script with no hint gets the conservative `PORTABLE_SERIAL_DEFAULT_WEIGHT_MS` default; every script in the current 160-script lane has a measured hint.
Hints only affect balance: the coverage guard keeps the partition complete and disjoint whatever they say, so a stale hint costs a slower shard rather than lost coverage.
Balance is still worth keeping current, because enough unmeasured scripts let one shard carry more than twice another shard's real work and reach the job cap while another runner sits idle.
That is not hypothetical: by 2026-09-01 the lane had grown from 116 to 139 scripts and from ~42 to ~63 minutes, 17 scripts were still unmeasured, and several hints were low by 2-5x, so shard 3 of 4 ran 17-20 minutes against its 20-minute cap while shard 1 ran 11.5 minutes and run [33574154856](https://github.com/kunchenguid/firstmate/actions/runs/33574154856) timed out seconds after a passing test.
`bin/fm-test-run.sh --check-coverage` now reports the unmeasured share as `serial_unhinted=` and refuses past `PORTABLE_SERIAL_MAX_UNHINTED_PERCENT`, so hint drift fails the coverage guard instead of silently pushing one shard into its job cap.
Refresh the hints whenever the serial lane gains scripts, rather than waiting for that bound to trip.

| Lane | Script count | Estimated duration |
|---|---:|---:|
| `portable-serial-1of5` | 29 | 1174943 ms (~19.58 min) |
| `portable-serial-2of5` | 32 | 1174941 ms (~19.58 min) |
| `portable-serial-3of5` | 33 | 1174977 ms (~19.58 min) |
| `portable-serial-4of5` | 33 | 1174966 ms (~19.58 min) |
| `portable-serial-5of5` | 33 | 1174958 ms (~19.58 min) |
| imbalance | | 36 ms |

The current table is generated from the runner's retained per-script maxima.
Before the refresh, the three green 2026-09-12 runs measured serial shard 1 at 1189355-1386292 ms (~19.82-23.10 min), while another serial shard completed in as little as 761714 ms (~12.70 min).
The refreshed weights distribute that measured work before the 30-minute job cap instead of concentrating it in shards 1 and 5.

The single longest script, `tests/fm-watch-triage.test.sh` at 597442 ms (~9.96 min), is the floor for any shard count.

Refresh the CI-derived hints by downloading the per-shard timing artifacts from several green CI runs, replacing the `portable_serial_weight_hints` table in `bin/fm-test-run.sh` with the slowest measured `duration_ms` per `path`, and updating the table above:

```sh
for run in <run-id> <run-id> <run-id>; do
  gh run download "$run" -R kunchenguid/firstmate --pattern 'fm-test-timing-portable-serial-*' -D "/tmp/fm-serial/$run"
done
jq -r '.scripts[] | [.path, .duration_ms] | @tsv' /tmp/fm-serial/*/*.json \
  | awk -F'\t' '$2 > m[$1] { m[$1] = $2 } END { for (p in m) print p, m[p] }' \
  | LC_ALL=C sort
bin/fm-test-run.sh --check-coverage
```

A timed-out shard uploads no artifact, so pick runs where every serial shard is green or the lane's slowest scripts go unmeasured in exactly the shard that needs them most.
Measure native-Windows-only scripts through the focused Git Bash runner and retain that `duration_ms` separately, because the portable CI shards skip them.

## Coverage guard

`bin/fm-test-run.sh --check-coverage` verifies that both parallel lanes partition the proven-isolated set.
It also verifies that the parallel lanes, portable serial lane, and real-Herdr family are disjoint and cover every `tests/*.test.sh` script.
It separately verifies that the portable serial CI shards are non-empty, disjoint, and together equal the portable serial lane.
It reports the unmeasured serial share as `serial_unhinted=` and refuses when that share exceeds `PORTABLE_SERIAL_MAX_UNHINTED_PERCENT`, so the shards stay balanced on evidence rather than on the default weight.

## Timing artifacts

Portable shards, each portable serial shard, and the Herdr lane upload runner-generated timing JSON.
`bin/fm-test-run.sh --aggregate-json` creates the combined summary artifact.
`.github/workflows/ci.yml` owns the exact artifact names and aggregation wiring.

## Local entry points

[CONTRIBUTING.md](../CONTRIBUTING.md) owns the local test policy and common entry points.
`bin/fm-test-run.sh --help` owns exact lane names, selection flags, and bounded `--jobs` mechanics.

## Timeouts

| Lane | Bound | Rationale |
|---|---|---|
| portable parallel 1/2 | per-script 360 s; runner wall 480000 ms; job `timeout-minutes: 10` | Recent per-case maxima rebalance to about 6.7 minutes per shard. The runner reports an ordinary failure before the job cap can cancel the verdict or timing artifact. |
| portable serial 1-5 | per-script 900 s; runner wall 1620000 ms; job `timeout-minutes: 30` | Recent per-case maxima rebalance to about 19.6 minutes per shard. A single-case stall fails within 15 minutes and a completed over-budget shard fails at 27 minutes, both before the job cap. |
| Herdr | family-run step `timeout-minutes: 20`; job `timeout-minutes: 75` backstop | Healthy runs finished around 7 minutes before this lane gained `fm-backend-herdr-focus-flash-e2e`, which measures about 2 minutes against a real lab locally, so the step bound is still the hang tripwire (cleanup and timing artifacts still upload) while the job cap stays a last-resort backstop. Refresh this figure from the lane's uploaded timing artifact. |

Timeouts are hang tripwires rather than expected healthy durations.
`.github/workflows/ci.yml` owns the exact numbers.
