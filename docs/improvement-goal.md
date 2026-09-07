# Speed, reliability, and update success goal

The goal is at least a 90% improvement against a measured baseline for every
supported workflow. This includes actual Hermes Agent updates. The goal remains
open until measurements support each claim.

## Execution goals approved on 2026-09-07

The next goals require operational results, not another synthetic speed claim:

1. Complete a managed Hermes Agent update. Verify the frozen target, all required
   tests, absent pending markers, and the selected services after promotion.
2. Install the tested updater improvements. Verify installed file identity,
   preserved settings and pending evidence, and plan availability during an update.
3. Verify fast detection of recurring failures without accepting old passing
   results. Keep the repeatable benchmark and record the actual update timings
   separately.

The user approved at most four managed source-repair attempts for a new update
transaction. Each attempt retains the ten-minute timeout and sandbox. A temporary
service override supplies this approval. It remains in place through retries and
is removed after the transaction reaches a terminal result. The saved default
repair limit remains zero. The rejected transaction is preserved by the updater.

Updater generation `81dcbaa69f9786076e8a` was installed after 188 tests passed on
each supported Python version. The saved configuration and repair marker hashes
were unchanged after installation. The installed `hermes-updates plan` command
reported the complete component plan while the new managed update was active.

### Managed result

The managed update completed successfully on 2026-09-07 at 03:44:15 UTC. It ran
for 32 minutes 49 seconds, including one source-repair attempt, full verification,
dependency deployment, configuration migration, and service restarts.

- Frozen target: `693641aa8b4359c602283bdbbc14041e03bc47bc`.
- Installed commit: `4a3f4f71141b850396a26deaea8ee219fa257f4b`.
- Python verification: 3,744 files, 45,519 passed tests, no failed tests, and
  412 skipped tests. Node checks and the web build also passed.
- Peak memory: 5,787,815,936 bytes. Swap peak: zero.
- Repair count: one. The repair changed two stale tests, not product code.
- Post-update checks: successful terminal service result, clean production,
  absent pending markers, correct gateway process IDs and source commits,
  dashboard HTTP health, dashboard restart, safe SQLite, and preserved connections.
- The temporary repair override was removed after completion. The default
  repair limit remains zero.

The WAL test now checks the existing PASSIVE checkpoint behavior. The search
test traces the actual pooled reader. An independent check in a separate copy
confirmed that disabling checkpoint execution makes the repaired WAL test fail.
Restoring execution makes it pass again.

The Buzz adapter was retrying before the update and remains so. Core gateway,
dashboard, and storage health passed. No provider request or authenticated chat
was used as proof of this update.

Recurring failures were observed about 41 seconds after the new service start,
compared with 22 minutes 49 seconds in the earlier run. The Python failure batch
took 17.0 seconds rather than 1,343.4 seconds. These are different upstream
targets, so this comparison is operational evidence, not a controlled benchmark.

`tests/verify_managed_update.py` records the repeatable post-update checks. It
requires a baseline report for a passing proof. The baseline detects lost
connections and a missing dashboard restart after source changes. An active service with a nominal success result,
pending state, a stale receipt, or incomplete observations cannot produce a pass.

## Acceptance measures

| Measure | Target |
| --- | --- |
| Elapsed time | At most 10% of baseline median and 95th-percentile time for each comparable workflow |
| Operational failures | At most 10% of the baseline failure rate |
| Updates without a verified, healthy target version | At most 10% of the baseline failure rate |
| Runs that need manual recovery | At most 10% of the baseline rate |

A 90% reduction in failures raises a 95% success rate to 99.5%. It does not mean
a 90-percentage-point increase. A zero-failure baseline has no measurable relative
reduction. Such a case needs more observations, not an improvement claim.

## Required coverage

- Fresh installation and reinstallation, including optional skill links.
- Configuration, component selection, and plans.
- Schedule changes and scheduled execution.
- Target checks, no-op updates, and complete source updates.
- Dependency setup, candidate tests, web checks, and web builds.
- Source repair, interrupted verification, and rejected target replacement.
- Promotion, dependency deployment, configuration migration, and runtime repair.
- Gateway and dashboard restart, profile health, and HTTP health.
- Interrupted deployment and failed-update reporting.

The updater uses forward recovery after promotion. A destructive production
rollback is not a supported workflow and is not part of this goal.

## Evidence rules

Comparisons require equivalent inputs, test coverage, cache state, hardware, and
resource limits. Cold runs, warm runs, no-op runs, and recovery runs are separate
workloads. A fast failure is still an update failure.

Each measurement records the workload, code revision or digest, sample count,
elapsed times, exit statuses, and test execution counts where applicable. Three
samples can support a preliminary median comparison, not a stable tail-latency
or fleet reliability claim. Production rates need counts and statistical
uncertainty over comparable update attempts.

An actual successful update requires a successful terminal service result, no
pending markers, the recorded target in production, and passing runtime and
service health checks. Tests in temporary repositories do not meet this condition.

Fixed targets, verification coverage, repair budgets, and systemd resource limits
remain unchanged. Direct `hermes update` execution and production test matrices
remain prohibited on the managed host.

## Initial measurements

The initial recovery measurement reduced median time from 0.470 to 0.248 seconds.
That is a 47.3% reduction for one interrupted verification workload.

The failure-order benchmark uses a copy of the upstream parallel test runner.
Its synthetic workload has one known failing file and 24 one-second passing
files. Each sample starts with a fresh candidate and a prior failed-file hint.
There are no cached passing results. With four workers and three samples per
version, median failure time decreased from 6.974 to 0.282 seconds, or 96.0%.
Test executions decreased from 26 to 2. Both versions returned failure.

The fixed-candidate control executed all 25 test files and passed. The
empty-former-failure control checked the empty file, executed the other 24 files,
and passed. These results support a failure-detection speed claim for this
workload only. They do not establish faster successful production updates.

`tests/benchmark_failure_order.py` contains the repeatable measurement. Its
`--help` output lists the required runner, baseline helper, output file, and
temporary directory arguments. It copies the runner into temporary repositories
and does not update Hermes Agent.

The installation fault tests cover command links, unit links, and unit settings
for both first installation and upgrade. All six initially selected the new
release before reporting the injected write failure. All six now preserve the
release selection and complete a retry. This does not establish atomic rollback
of configuration or systemd operations.

Two more fault tests verify that failed installation restores an existing command
file or symlink. The file test also verifies its permissions. New command links
are removed after failure, so first-install failures do not leave dangling commands.

## Plan availability during an update

The previous `hermes-updates plan` could not report resolved actions while an
update held the lock. A live check on 2026-09-06 returned exit status 75. The
changed command returned the full component plan during that same managed run.
The plan no longer takes the update lock. The regression test verifies that the
plan leaves saved state unchanged and that a competing update still stops.

## Managed-update baseline on 2026-09-06

The installed updater ran through its managed systemd service from 19:52:39 to
20:15:28 UTC. The local changes described above were not installed for this run.

- Elapsed time: 22 minutes 49 seconds.
- Target: `6b2d4faf36f107c364048e2fe17eaa02154e9ea9`.
- Unchanged production: `93a9c9d3a6252c5bef9c95da4ed91114d20727cd`.
- Verification: 3,738 files, 45,489 passed tests, 2 failed tests, and 412 skipped tests.
- Verification time: 1,343.4 seconds with four workers.
- Terminal result: exit status 78, with repair pending and no promotion pending.
- Source-repair attempts: zero, with a configured limit of zero.
- Peak memory: 4,008,128,512 bytes. CPU time: 4,415.293244 seconds.

The failed tests were
`tests/hermes_cli/test_kanban_db_repair.py::test_wal_checkpoint_truncates_wal_file`
and
`tests/test_hermes_state.py::TestFTS5Search::test_search_projection_skips_context_enrichment_queries`.
The failed transaction remains available for diagnosis. The update did not
succeed, and no automatic repeat was started. Changing the source-repair limit
requires a separate user instruction.
