---
name: update-hermes-agent
description: Update an existing Hermes Agent installation with its configured scope, verified candidate, and saved recovery state. Supports partially configured and CLI-only installations.
version: 3.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [update, recovery, hermes-agent]
    related_skills: [hermes-weekly-update]
---

# Update Hermes Agent

Read the installed package README and `hermes-weekly-update` operator skill.
Use this package's configured installation and scope. Do not assume a provider,
gateway, dashboard, named profile, Codex authentication, or schedule exists.
Do not invent a remote host or a host-specific connection command.

## Run

1. Use `hermes-updates config` and `hermes-updates plan` to inspect the installation,
   selected actions, and optional schedule. Check the configured state directory
   and any running updater before starting another run.
2. Use `hermes-updates run` to start and wait for a bounded update. For an existing
   scheduled service run, monitor `hermes-weekly-update.service` and its journal.
   The lock prevents concurrent updates. On installations explicitly configured
   without systemd, use `hermes-updates run --direct` with caller-managed limits.
3. Resume pending work with the same settings and target. The scheduled service
   owns automatic retries; do not race its restart delay or reset failed counters.
   A manual run may be repeated within the saved retry budget.
4. Report success only after the run exits successfully, pending markers are
   absent, and the installed source contains the recorded target. Check only
   the selected components and services captured by the transaction. A disabled
   timer or absent gateway is valid.

## Scope and repairs

Source advances as one shared Git commit. Profile selection scopes migration,
cache invalidation, and gateway restarts, not independent profile source versions.
Deployment options do not weaken candidate verification. Dependencies, dashboard
builds, migration, gateway maintenance, and runtime repair are individually
configurable. Never expand scope to clear a failed check without authorization.

Codex repair is opt-in; zero attempts is the default. Dependency/tooling failures
must not trigger speculative source edits. Failed candidates, logs, and repair
counters remain available for diagnosis. A safe terminal stop is an update failure.

Never run the full test matrix in the installed checkout, manually remove pending
state, reset production Git, or bypass saved budgets. Use the package's update
path rather than launching a competing `hermes update` transaction.

Report the result, target and installed SHAs, relevant verification evidence,
repair count, pending state, selected service health, and schedule if enabled.
