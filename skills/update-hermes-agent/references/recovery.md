# Recovery and scheduled runs

Read the effective configuration with `hermes-updates config`. The historical
service and default state names remain stable so upgrades preserve schedules,
locks, and interrupted transactions. `hermes-weekly-update` is no longer a separate
skill; scheduling is optional.

## Inspect an existing run

```sh
systemctl --user show hermes-weekly-update.service -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts
journalctl --user -u hermes-weekly-update.service -n 40 --no-pager
systemctl --user status hermes-weekly-update.timer
```

Manual systemd runs use transient `hermes-updates-manual-*` units. Monitor the
unit that owns the run. A scheduled service's automatic restart owns retries;
wait through its restart delay. When no updater or automatic restart is active,
resume with `hermes-updates run` and the same effective settings. Direct mode
retains the same lock and evidence but needs caller-managed resource limits.

## Read saved evidence

Use `HERMES_UPDATE_STATE_DIR` from configuration; its default is
`~/.local/state/hermes-weekly-update`. Inspect `last-result`, candidate summaries,
verification logs, and these transaction files:

| File | Meaning and next action |
| --- | --- |
| `repair-pending` | Frozen target and candidate checkpoint; resume that candidate before selecting another target. |
| `promotion-pending` | Previous, target, and promoted source; finish deployment and health checks even if source already advanced. |
| `deployment-profiles` | Saved component scope and active service inventory; recovery uses it even if interruption stopped those services. |

Production deployment recovery takes precedence. Use recorded paths and SHAs,
and inspect the installed checkout with read-only Git commands. A rejected
candidate may be archived by a later default/scheduled run when upstream changes
or it ages out; an explicit target SHA remains pinned.

Compatible retries reuse test evidence. Changes to source, shared fixtures,
dependencies, interpreter, or verifier invalidate affected cached results.
Toolchain and dependency failures stop without speculative source repair. The
configured repair agent operates only when the saved budget permits; each attempt
is bounded to ten minutes. Candidate and deployment budgets persist across starts.

Preserve markers, counters, and logs. Never delete them, reset production Git,
automate `systemctl reset-failed`, or expand disabled components to clear a failed
gate. Report a blocked target with its evidence when the updater cannot resume
within the current settings and budget.

## Verify completion

Confirm successful exit, no pending markers, and target ancestry in the installed
source. Check only selected installed components against the saved service
inventory. CLI-only updates need no gateway; dashboard updates need no gateway
HTTP endpoints. Use gateway processes and version receipts when supported by
upstream, and report unavailable health evidence accurately.
