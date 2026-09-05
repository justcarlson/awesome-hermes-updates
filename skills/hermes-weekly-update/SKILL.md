---
name: hermes-weekly-update
description: Operate Awesome Hermes Updates: inspect configuration, schedules, verified promotion, and bounded recovery for any supported Hermes setup.
version: 3.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [update, scheduling, recovery, hermes-agent]
    related_skills: [update-hermes-agent]
---

# Hermes Update Operations

The historical skill, unit, and state-directory names remain for compatibility.
Scheduling is optional. Read the installed README at
`~/.local/share/awesome-hermes-updates/current/README.md` for supported paths and
settings. Do not assume the default Hermes home or a fully configured installation.

## Inspect and start

```sh
hermes-updates config
hermes-updates plan
hermes-updates run --check
hermes-updates run
```

`config` reports effective settings including per-run environment overrides.
`plan` resolves installed capabilities without fetching. `run --check` selects
an upstream commit without promotion; an existing pending target takes precedence.
`run` uses a transient user service, waits, and streams output. Direct mode is
available on Linux without systemd when the caller supplies process limits.

For a scheduled update, inspect the persistent service:

```sh
systemctl --user show hermes-weekly-update.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts
journalctl --user -u hermes-weekly-update.service -n 40 --no-pager
systemctl --user status hermes-weekly-update.timer
```

Monitor an existing run or pending automatic restart. When inactive, the service
can be started with `systemctl --user start hermes-weekly-update.service --no-block`.
Do not race restarts with another updater or automate `reset-failed`.

## Configuration and scheduling

`hermes-updates configure` saves paths, component switches, profiles, extras,
upstream ref, and the repair limit. Every component supports `auto`, `on`, and
`off`. Defaults detect installed capabilities; no configuration files, services,
or provider credentials are required for a source-only installation.

`hermes-updates schedule 'daily'` enables a validated calendar.
`hermes-updates schedule on-demand` stops scheduled execution. Existing schedules
survive reinstall. The next timer run is relevant only when scheduling is enabled.

Do not change scope during an unfinished transaction. Source and dependencies
are shared by profiles; selected profile names only narrow profile operations.
Explicitly disabled installed dependencies block targets that change their
manifests. Runtime repair cannot stop user-excluded active services.

## Recovery and evidence

Use `HERMES_UPDATE_STATE_DIR` from effective configuration (default
`~/.local/state/hermes-weekly-update`). Inspect `last-result`, candidate summaries,
verification logs, `repair-pending`, `promotion-pending`, and `deployment-profiles`.

- `repair-pending` records a fixed target and candidate checkpoint. Resume that
  candidate before selecting another upstream commit.
- `promotion-pending` records the previous, target, and promoted source. Resume
  deployment and health checks on that commit, even if source already advanced.
- `deployment-profiles` saves resolved scope and the active services to maintain.
  Restarts use the saved inventory even if an interrupted deployment stopped them.
- Dependency/toolchain failures stop without spending source-repair attempts.
  Repairs default to zero; opted-in attempts last at most ten minutes each.
- Test evidence survives compatible retries. Shared code, fixtures, dependencies,
  interpreter, or verifier changes invalidate cached results. Source changes
  during verification block promotion; generated test artifacts are archived.
- A rejected target can be archived by a later scheduled/default run when upstream
  changes, or when the candidate ages out. Explicit target SHA overrides stay
  pinned. Production deployment recovery always takes precedence.

The persistent service limits each start to six CPU cores, 8 GiB memory, no swap,
and four hours. Candidate retries and deployment attempts remain bounded across
starts. Never remove markers, counters, or evidence to reset those limits.

## Completion

Prove successful exit, absent pending markers, recorded target ancestry, and
health for selected installed components. CLI-only updates do not need a gateway;
dashboard updates do not need gateway HTTP endpoints. Validate selected gateway
processes and version receipts when supported by upstream. Report any unavailable
health evidence accurately.

Do not run full verification in the installed checkout or rewrite its history.
Do not claim a successful update after a safe stop. Include target and installed
SHA, verification log, repair count, relevant service state, and optional next run
in the result.
