---
name: update-hermes-agent
description: Configure, run, schedule, or recover verified Hermes Agent updates on Linux with Awesome Hermes Updates. Use for existing installations, including unconfigured checkouts and CLI-only setups.
---

# Update Hermes Agent

Operate the installed `hermes-updates` command with the user's configured scope.
This skill works in Codex and Claude Code; the updater currently supports Linux.
Keep the selected target, saved recovery state, and attempt budgets across retries.

## Inspect

Run `hermes-updates config` and `hermes-updates plan`. These report effective
settings and installed capabilities without fetching upstream or changing Hermes.
Read `hermes-updates --help` for commands and subcommand help for options.

If the command is missing, use the package README's installation instructions
from the available checkout. An installed copy lives at
`~/.local/share/awesome-hermes-updates/current/README.md`. Installation paths and
skill paths are distinct; installing a plugin alone does not install the updater.

Check the configured `HERMES_UPDATE_STATE_DIR` and any active updater. If there
is pending state, a failed run, or a scheduled service awaiting restart, read
[recovery](references/recovery.md) before proceeding. Monitor the existing run
until its result is known.

## Configure or schedule

When requested, apply only the user's changes with `hermes-updates configure`
or `hermes-updates schedule`. Preserve all other settings. Finish pending recovery
before changing deployment scope. Re-read `config` and `plan` to verify the result;
a configuration-only request ends here.

Source advances as one shared Git commit. Profiles narrow migration, cache
invalidation, and gateway restarts; they do not select independent source versions.
Auto settings detect installed components. An absent provider, gateway, dashboard,
or schedule is valid. Repairs are optional and default to zero attempts; enabling
them or expanding component scope requires the user's requested change.

## Run and report

Use `hermes-updates run --check` for a requested upstream check. It selects a target
without promotion and respects an existing pending target. A check-only request
ends with the target and pending status.

Use `hermes-updates run` for an update; it waits and streams output within systemd
resource limits. On a Linux installation configured without systemd, use
`hermes-updates run --direct` with caller-managed resource limits. Let the updater
own verification, promotion, locking, and retries. Use its separate candidate for
verification; never run the full test matrix in the installed checkout or launch
a competing `hermes update` transaction.

Success requires a successful exit, absent `repair-pending` and `promotion-pending`
markers, installed source containing the recorded target, and health evidence for
the selected components. For a failure or interruption, use the recovery reference
and preserve the evidence. A safe terminal stop is a failed update.

Report the result, target and installed SHAs, verification log or receipt, repair
count, pending state, and selected service health. Include the next scheduled run
only when scheduling is enabled. Identify unavailable evidence explicitly.
