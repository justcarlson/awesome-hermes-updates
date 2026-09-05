---
name: hermes-weekly-update
description: Use when asked to launch, monitor, recover, or verify the managed Hermes Agent update. Preserve its fixed target and bounded recovery state through completion or a safe terminal stop.
---

# Hermes Weekly Update

## Contract

Read `~/.local/share/awesome-hermes-updates/current/README.md` first. Use only the managed
`hermes-weekly-update.service`. The Awesome Hermes Updates repository owns the updater and its units. This skill operates the installed service.
Unattended operation means automatic bounded recovery or a safe stop with
evidence. It does not guarantee that Codex can repair every upstream defect.

## Launch and Monitor

1. On `hermes-agent`, use local user-systemd commands. Else use `sc-remote
   source`, `sc-remote list`, `sc-remote check hermes-agent`, then
   `sc-remote run hermes-agent -- <command>`.
2. Inspect the service, timer, production Git state, and pending markers:

   ```bash
   systemctl --user show hermes-weekly-update.service \
     -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts
   systemctl --user is-enabled hermes-weekly-update.timer
   git -C "$HOME/.hermes/hermes-agent" status --short --branch
   journalctl --user -u hermes-weekly-update.service -n 30 --no-pager
   ```

3. If the service is active, activating, or waiting for an automatic restart,
   monitor that run. If it is inactive or failed, start once:

   ```bash
   systemctl --user start hermes-weekly-update.service --no-block
   ```

   Read markers and summaries under `~/.local/state/hermes-weekly-update/`.
   Do not start a second update. Do not use `reset-failed` in an automatic loop.
   The service owns recovery and replacement of failed targets. Exit 78 is a
   failed transaction, not success. Preserve its evidence; do not retry in a loop.
4. Poll the same unit with the command above. Add `MemoryPeak` and
   `CPUUsageNSec` for the final report. Keep the task open through scheduled
   retries. A shell timeout or an accepted start request is not completion.

## Target and Recovery

- The service selects `main` by default. `HERMES_UPDATE_REF` can select an
  explicit release tag. Selection is frozen as a full SHA before candidate work.
- `repair-pending` owns the saved target, production SHA, candidate, and phase.
  Resume this state without a new fetch or a new target, including after reboot.
- `HERMES_UPDATE_TARGET_SHA` accepts a full commit already present locally.
  Configure it in a reviewed service override before launch. Keep the override
  until saved state or terminal no-op evidence records that SHA, then remove it and
  reload the units. Never set and immediately unset the manager environment
  around `start --no-block`; the service can miss that value.
- systemd retries after two minutes. Each candidate has four service attempts
  per installed updater revision; total starts and source-repair counts persist. Successful on-demand checks do not use that budget. The recovery unit resumes
  pending work at user-manager startup; the persistent timer catches missed runs.
- Dependency or runtime setup failure 69 retries without Codex. Candidate repair has a
  10-minute timeout and a saved total limit of four attempts by default.
  `HERMES_UPDATE_MAX_REPAIRS` accepts 0 through 20. A repair without progress
  saves a terminal block. A later launch can archive that failed transaction
  and select a different upstream SHA. The same rejected SHA gets no new source-repair budget.
  Candidates older than six hours may also be replaced. Explicit SHA overrides
  stay pinned. Production promotion recovery always takes precedence.
- `promotion-pending` means deployment or health checks still need completion.
  Recover through the same service. Do not reset production Git for rollback.

## Verification and Completion

The service uses private HOME and a fresh `/tmp` for each check stage. It retries
failed files once and saves per-file results. Test-only repairs rerun changed and failed files. Runtime, shared-fixture,
or dependency changes invalidate the cache. Known failures run first. Generated
SQLite, lock, and log files are archived; source changes still block promotion.
The service checks unchanged HEAD and builds
web assets before promotion. It records active profiles before deployment, then
repairs unsafe SQLite with a saved package snapshot, migrates configuration,
refreshes units, restarts services, and checks each profile's process and SHA.
Deployment has four attempts per controller revision; native checks also run on no-op requests.

Prove the unit result is `success`; both pending markers are absent; production
contains the recorded target; `hermes --version` agrees with the installed code;
gateway, dashboard, recorded active profiles, and HTTP checks pass; and the
timer is enabled. Do not select a newer target because version output mentions
one. Read saved logs and candidate summaries for test and repair evidence.

## Safety and Output

- Never run `hermes update` or a full repository test matrix in production.
- Never reset or rewrite the production checkout to clear a failure.
- Never manually delete pending markers, counters, or failed-state evidence.
  Only the updater may archive a rejected transaction before selecting a new SHA.
- Never bypass the service CPU, memory, swap, timeout, or OOM limits.
- Do not resurrect `weekly-hermes-agent-codex-goal`.

Report the terminal result, full target and installed SHA, tests, repair count,
pending state, service and HTTP health, peak resources, next timer run, and any
remaining risk. A bounded safe stop is a reported failure, not update success.
