---
name: update-hermes-agent
description: Use when asked to update Hermes Agent. Launch and monitor its managed service with one fixed upstream target, bounded repair, and automatic recovery until success or a safe terminal stop.
version: 2.4.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [git, update, recovery, hermes-agent, codex, pi]
    related_skills: [hermes-weekly-update, hermes-agent]
---

# Update Hermes Agent

Use this skill as the only requested command for a managed Hermes Agent update.
Read `~/.local/share/awesome-hermes-updates/current/README.md` and load `hermes-weekly-update`.
That skill supplies the host route, checks, limits, and recovery procedure.
This skill does not update arbitrary Git repositories.

## Execute the Request

1. Inspect the service and `~/.local/state/hermes-weekly-update/` before launch.
   Use the local
   user-systemd manager on Hermes Agent. From another host, start with
   `sc-remote source` and `sc-remote list`, check `hermes-agent`, and use
   `sc-remote run hermes-agent -- <command>`.
2. Monitor an active run or scheduled automatic restart. When the unit is
   inactive or failed, start it once:

   ```bash
   systemctl --user start hermes-weekly-update.service --no-block
   ```

3. Continue to inspect the same unit and its journal:

   ```bash
   systemctl --user show hermes-weekly-update.service \
     -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts
   journalctl --user -u hermes-weekly-update.service -n 30 --no-pager
   ```

   systemd owns retries, including the two-minute delay. Do not start another
   run when a shell call ends. Do not automate `reset-failed` or bypass the
   four-attempt service limit for each candidate and installed updater revision.
   Total starts and source-repair counts remain recorded across revisions. Keep the user task open through retries.
4. Report a failed transaction on exit 78. A future launch can archive it and
   select a different upstream commit; the same rejected SHA gets no new source-repair budget.
   The service also replaces candidates older than six hours. Explicit target
   overrides stay pinned. Do not erase evidence or change counters yourself.
5. On success, verify the full target SHA and its ancestry in production;
   installed version; final tests; absent pending markers; gateway, dashboard,
   and recorded active profile health; HTTP health; and the enabled timer.
   Read saved logs and candidate summaries. A successful launch is not proof
   of a successful update.

## Fixed Target

The default source is `main`. An explicit release tag can be configured through
`HERMES_UPDATE_REF`. The service resolves the source once and saves the full
commit SHA. `repair-pending` and `promotion-pending` take precedence over new
selection. Resume their target without fetching or checking newer upstream work.
The recovery unit resumes pending work after user-manager startup. The persistent
weekly timer catches missed scheduled runs.

An explicit `HERMES_UPDATE_TARGET_SHA` must name a full commit already present
in the local repository. Use a reviewed service override before launch, retain
it until saved state or a terminal no-op proves selection, then remove it and
reload units. Do not set and immediately unset the manager environment around
`start --no-block`; startup can read the environment after it was cleared.

## Bounded Repair and Deployment

Dependency setup failure 69 receives service retries without a Codex repair.
Source repair has a 10-minute timeout and four total attempts per candidate by
default. Its counter survives restarts. `HERMES_UPDATE_MAX_REPAIRS` supports 0
through 20, but a blocked candidate must not bypass its recorded limit.
A repair that makes no progress saves a terminal block.

The service uses private test HOME, a fresh `/tmp` for each check stage, one
test-file retry, and cached dependencies.
Per-file results survive test-only repairs; runtime and shared-fixture changes
invalidate them. Failed files run first. Generated data files are archived. It checks that verification did not change
HEAD or leave uncommitted files. Web assets must build before promotion.
The service records active profiles, migrates configuration, refreshes service
definitions, restarts services, and checks their processes and installed SHA.
Pending promotion recovery finishes deployment on the recorded commit.
The runtime check also runs when Git is current. Upstream stages a safe SQLite
runtime; the wrapper preserves installed extras and checks dependencies before
restarting services. Deployment has four attempts per controller revision.

Unattended operation means bounded automatic recovery or a safe stop with useful
evidence. It cannot guarantee repair of every upstream defect or external outage.

## Safety and Report

Never run `hermes update`, invoke the updater script outside its managed service,
or run the full matrix in production. Never manually delete pending markers or repair
state, rewrite production Git, or bypass resource limits. Do not launch a
second detached Codex goal to operate the same update.

Report the terminal result, full target and production SHAs, tests, repair count,
pending markers, service and HTTP health, peak resources, next timer run, and
remaining risks. Distinguish an update success from a safe terminal stop.
