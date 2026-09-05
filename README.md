# Awesome Hermes Updates

Verified updates for Hermes Agent, on demand or on your schedule.

Use the Hermes installation you have: a checkout awaiting setup, a CLI with no
provider configured, or an installation running gateways and a dashboard. This
package discovers installed components and lets you choose which deployment
steps to perform. It does not require a completed Hermes setup or install Hermes
for you.

Each update fixes one upstream commit as its target, verifies a separate
candidate, and promotes it only after the checks pass. Interrupted work resumes
from saved state. Optional Codex or Claude Code repairs have an explicit attempt limit.

## Install

Supported: Linux, system Python 3.11+, Bash, Git, and a writable Hermes Git checkout.
The default checkout is `~/.hermes/hermes-agent`; an installed Python environment
is detected at `<checkout>/venv`. A user systemd manager provides scheduling and
resource limits. Custom checkout and Hermes data paths are supported. macOS and
other platforms are not yet supported; the installer rejects them explicitly.

```sh
gh repo clone justcarlson/awesome-hermes-updates ~/projects/awesome-hermes-updates
cd ~/projects/awesome-hermes-updates
./install
```

For a different installation:

```sh
./install --repo /path/to/hermes-agent --hermes-home /path/to/hermes-data
```

The installer can run before Hermes is configured. Native candidate verification
needs uv and Bubblewrap with working namespaces; targets with a frontend also
need Node/npm. These are updater tools, not requirements to configure a gateway,
provider, or dashboard. An authenticated Codex or Claude Code CLI is needed only
if you opt into source repairs; normal updates work without either client.
Unsupported target verifier layouts stop with an explanation and saved evidence.

New installations are **on demand**. Reinstallation preserves your settings,
existing schedule, and recovery state. Package files are installed as an immutable
generation with an atomic `current` link. Previous generations remain available.

## Choose what to update

```sh
hermes-updates plan
hermes-updates config
hermes-updates configure --dashboard off --migrate off
hermes-updates configure --profiles default,work --extras anthropic
hermes-updates run --check
hermes-updates run
```

`plan` shows configured options and resolved actions without fetching upstream
or changing Hermes. `run --check` reports the selected upstream target without
promoting it. `run` waits for the result and streams output within a systemd
resource boundary.

| Option | `auto` (default) | `off` |
| --- | --- | --- |
| `--python` | Sync an existing `venv`, preserving separately installed packages | Leave Python dependencies unchanged |
| `--node` | Refresh existing Node dependencies, or prepare them for an installed dashboard | Leave Node dependencies unchanged |
| `--dashboard` | Rebuild an installed dashboard when Node updates are selected; restart it if running | Leave dashboard build and service alone |
| `--gateway` | Restart selected gateways that were running | Leave gateway services alone |
| `--migrate` | Migrate existing configuration files for selected profiles | Leave configuration files alone |
| `--runtime` | Check and repair the installed Python SQLite runtime if necessary | Leave the runtime alone |

`on` explicitly requests an applicable operation; missing capabilities stop the
run before promotion. Node and dashboard `on` can prepare those components when
the checkout includes their manifests. Gateway `on` requires a running selected
gateway; updates never start an inactive service. A blank profile is valid.

`--profiles auto` selects existing profile homes. A comma-separated list scopes
configuration migration, update-status cache invalidation, and gateway restarts;
`default` means the main Hermes home. Dashboard and dependencies are shared.
`--extras` chooses additional optional Python extras; the default is core
requirements without expanding the installation to every integration.

**Source always advances as a whole Git commit.** All profiles using that checkout
see the same source. These options limit dependency, configuration, runtime, and
service actions; they do not pin individual source folders or profile versions.
If installed dependencies are disabled and the target changes their manifests,
the updater stops before promotion. Runtime repair also requires permission to
maintain every running service that uses the environment.

A checkout with no Python environment or running services receives a verified
source update without creating those components. Existing user skills, provider
credentials, and unrelated services are not synchronized or installed.

## Set a schedule

```sh
hermes-updates schedule 'daily'
hermes-updates schedule 'Sun *-*-* 03:15:00 America/New_York'
hermes-updates schedule on-demand
```

Calendars use systemd syntax and are validated before saving. Include a timezone
when you need one; otherwise the host timezone applies. Missed scheduled runs
are persistent. For runs without a login, enable user lingering with
`loginctl enable-linger "$USER"`.

Existing `hermes-weekly-update` service and timer names remain compatible:

```sh
systemctl --user status hermes-weekly-update.timer
journalctl --user -u hermes-weekly-update.service -n 40
```

On Linux without a user systemd manager, install with `./install --no-systemd`,
configure with `hermes-updates configure --no-systemd ...`, and use
`hermes-updates run --direct`. Direct execution retains locking and verification,
but the caller supplies resource limits and any external scheduling.

## Settings and recovery

Settings live in `~/.config/awesome-hermes-updates/config`. The CLI writes literal
assignments; shell commands and variable substitutions are not evaluated.
Environment variables override saved settings for an individual run. Paths are
absolute or start with `~/`. `HERMES_HOME` supplies the initial Hermes data root;
explicit `HERMES_UPDATE_HOME` and `HERMES_UPDATE_REPO` take precedence.

CLI options map to `HERMES_UPDATE_PYTHON`, `HERMES_UPDATE_NODE`,
`HERMES_UPDATE_DASHBOARD`, `HERMES_UPDATE_GATEWAY`, `HERMES_UPDATE_MIGRATE`,
`HERMES_UPDATE_RUNTIME`, `HERMES_UPDATE_PROFILES`, and `HERMES_UPDATE_EXTRAS`.
Use `hermes-updates configure --ref TAG` to select a ref instead of upstream
`main`. `HERMES_UPDATE_TARGET_SHA` selects a full commit already fetched locally.
Saved transactions take precedence over a newly requested target.

Source repair defaults to zero attempts. Choose an authenticated repair CLI explicitly:

```sh
hermes-updates configure --repair-agent codex --max-repairs 4
# Or use Claude Code:
hermes-updates configure --repair-agent claude --max-repairs 4
```

`HERMES_UPDATE_REPAIR_AGENT` selects `codex` (the compatibility default) or `claude`.
Both use the same fixed candidate, verification gate, and saved attempt budget.
Each attempt has ten minutes.
Claude repairs require Claude Code 2.1.246+, Bubblewrap, and socat. Complete login
with `CLAUDE_CONFIG_DIR="$HOME/.claude" claude auth login` before unattended use;
the updater uses that directory for credentials and writable CLI state.
Systemd runs allow four test workers, six CPU cores, 8 GiB memory, no swap, and
four hours per run. Deployment retries and source repair budgets are bounded.

Logs, receipts, candidates, and pending state live in
`~/.local/state/hermes-weekly-update/`. Recovery preserves the selected actions
and running-service inventory. Finish recovery with the same settings before
changing scope; never delete pending markers or reset the installed checkout to
clear a failure. Inspect the saved verification log for an unsupported target
or failed gate. A bounded stop is a failure with evidence, not a successful update.

Reinstall after `git pull --ff-only` to update this package. The
[operator skill](skills/update-hermes-agent/SKILL.md) describes the workflow, with
[recovery details](skills/update-hermes-agent/references/recovery.md) loaded when
needed. Earlier implementation research is in [docs/research.md](docs/research.md).

## Use with Codex or Claude Code

Install the updater and its single `update-hermes-agent` skill for either client:

```sh
./install --skills both
# Or: ./install --skills codex
# Or: ./install --skills claude
```

The installer links the skill into `~/.agents/skills/` for Codex and
`~/.claude/skills/` for Claude Code. Both links follow the installed package's
atomic `current` generation. Restart the client if it has not discovered the skill.
Invoke `$update-hermes-agent` in Codex or `/update-hermes-agent` in Claude Code,
or ask the agent to configure, check, run, schedule, or recover a Hermes update.

Omitting `--skills` preserves your previous selection; first installations default
to no client links. `--skills none` removes only links this installer owns.
User-edited copies are preserved: move an existing conflicting copy aside before
selecting that client. If you manually installed the old `hermes-weekly-update`
skill, remove that obsolete copy from your skill store after switching to
`update-hermes-agent`. Its recovery guidance now lives beside the main skill.

For project-scoped installation, place the complete skill directory in
`.agents/skills/` (Codex) or `.claude/skills/` (Claude Code). Native plugin manifests
are also included for both clients and share the same `skills/` directory. Choose
one skill installation method per client to avoid duplicate discovery. Plugin
loading installs agent instructions; run `./install` separately for the updater.
See [client compatibility](docs/agent-compatibility.md) for verified requirements
and local plugin loading.

## Develop

```sh
./check
./install --stage /tmp/ahu-install-check
```

Tests use temporary repositories and fake deployment tools, including partial
installations, scope restrictions, schedules, and interrupted updates. The `check`
command runs shell syntax checks and the regression suite on Python 3.11 and 3.13.
