# Awesome Hermes Updates

**Verified updates. Saved progress. Bounded recovery.**

Update Hermes Agent on demand or on a schedule. Each run fixes one upstream
commit as its target, tests a separate candidate, and promotes it only after
verification. Failed checks can receive a bounded Codex repair. Interrupted
work resumes from saved state. A fault that cannot be repaired stops with logs.

## Install

For Linux with user systemd and Hermes Agent at `~/.hermes/hermes-agent`.
Requires Python 3.11+, Bash, Git, uv, Node/npm, Bubblewrap, jq, curl, and an
authenticated Codex CLI. The host must permit Bubblewrap namespaces.
The current deployment checks require `hermes-gateway.service`,
`hermes-dashboard.service`, gateway health on port 8644, and dashboard APIs on
port 9119. This package does not install Hermes or those services.

```sh
gh repo clone justcarlson/awesome-hermes-updates ~/projects/awesome-hermes-updates
cd ~/projects/awesome-hermes-updates
./install
```

Set `HERMES_UPDATE_DASHBOARD_HOST` in
`~/.config/awesome-hermes-updates/config` if the dashboard does not listen on
`127.0.0.1`. For schedules that must run without a login, enable user lingering:
`loginctl enable-linger "$USER"`.

The installer keeps settings and recovery state. It installs a complete package
generation, switches one link, and enables the Sunday 03:15 America/New_York
timer. It refuses to install during an update. Run `./install` again after
`git pull --ff-only` to install package changes. Previous generations remain
under `~/.local/share/awesome-hermes-updates/releases/`.

## Use

```sh
systemctl --user start hermes-weekly-update.service --no-block
systemctl --user status hermes-weekly-update.service
journalctl --user -u hermes-weekly-update.service -n 40
```

For an agent, install both folders in [`skills/`](skills/) into its skill store,
then ask it to use `update-hermes-agent`. The same service handles both paths.
Existing unit names are retained for compatibility; the schedule is optional.
Use `systemctl --user edit hermes-weekly-update.timer` to change it, or
`systemctl --user disable --now hermes-weekly-update.timer` for on-demand use.

The default target is upstream `main`. Set `HERMES_UPDATE_REF` for a tag, or
`HERMES_UPDATE_TARGET_SHA` for a full commit already fetched into the local
repository. Saved transactions take precedence. See the
[operator skill](skills/hermes-weekly-update/SKILL.md) for recovery and checks.

## Limits

The service permits four test workers, six CPU cores, 8 GiB of memory, no swap,
and four hours per start. Source repair has four attempts with ten minutes each.
Passing test-file results survive compatible retries. Deployment checks include
active profiles, installed code, HTTP health, and the Python SQLite runtime.
Logs and transaction state are in `~/.local/state/hermes-weekly-update/`.

Always start updates through systemd. Do not run `hermes update`, run the full
test suite in production, or delete pending state to clear a failure. Automatic
recovery can stop safely; it cannot promise to repair every upstream fault.

## Develop

```sh
uv run --group dev pytest -q
./install --stage /tmp/ahu-install-check
```

Tests use temporary repositories and fake deployment commands. They do not
update the installed harness. The controller is in `bin/`, service definitions
are in `systemd/`, and agent instructions are in `skills/`.
