from pathlib import Path
import os
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_UPDATER = REPO_ROOT / "bin/hermes-weekly-update"
WEEKLY_UPDATE_SERVICE = REPO_ROOT / "systemd/hermes-weekly-update.service"
WEEKLY_UPDATE_TIMER = WEEKLY_UPDATE_SERVICE.with_suffix(".timer")

def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def test_weekly_updater_exits_cleanly_when_upstream_is_already_present(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "README.md").write_text("current\n")
    _git("add", "README.md", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)

    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(home / "state"),
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "NO_UPDATE" in result.stdout
    assert _git("status", "--short", cwd=production) == ""


def test_weekly_updater_promotes_only_a_verified_candidate(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)

    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "upstream update", cwd=upstream)
    target_sha = _git("rev-parse", "HEAD", cwd=upstream)

    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(home / "state"),
        "HERMES_UPDATE_VERIFY_COMMAND": "/bin/true",
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROMOTED" in result.stdout
    assert _git("merge-base", "--is-ancestor", target_sha, "HEAD", cwd=production) == ""
    assert (production / "version.txt").read_text() == "two\n"
    assert _git("status", "--short", cwd=production) == ""


def test_weekly_updater_stops_when_default_python_gate_fails(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    fake_bin = home / "bin"
    upstream.mkdir()
    state.mkdir(parents=True)
    fake_bin.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    production_sha = _git("rev-parse", "HEAD", cwd=production)

    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "candidate", cwd=upstream)

    failing_python = home / "failing-python"
    failing_python.write_text("#!/bin/sh\nexit 41\n")
    failing_python.chmod(0o755)

    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        'cp "$FAKE_VERIFY_PYTHON" "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
        'chmod 0755 "$UV_PROJECT_ENVIRONMENT/bin/python"\n'
    )
    fake_uv.chmod(0o755)

    npm_check_marker = home / "npm-check-ran"
    fake_npm = (
        state
        / "toolchain"
        / "npm-12.0.2"
        / "node_modules"
        / ".bin"
        / "npm"
    )
    fake_npm.parent.mkdir(parents=True)
    fake_npm.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        "  printf '12.0.2\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "ci" ]; then\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "run" ] && [ "$2" = "check" ]; then\n'
        '  : > "$NPM_CHECK_MARKER"\n'
        "  exit 0\n"
        "fi\n"
        "exit 99\n"
    )
    fake_npm.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_VERIFY_PYTHON": str(failing_python),
        "NPM_CHECK_MARKER": str(npm_check_marker),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_REPAIR_COMMAND": "/bin/false",
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "PROMOTED" not in result.stdout
    assert not npm_check_marker.exists()
    assert _git("rev-parse", "HEAD", cwd=production) == production_sha
    assert (production / "version.txt").read_text() == "one\n"


def test_weekly_updater_recovers_when_marker_precedes_production_fast_forward(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    upstream.mkdir()
    state.mkdir(parents=True)

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    previous_sha = _git("rev-parse", "HEAD", cwd=production)

    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "candidate", cwd=upstream)
    candidate_sha = _git("rev-parse", "HEAD", cwd=upstream)
    _git("fetch", str(upstream), candidate_sha, cwd=production)
    (state / "promotion-pending").write_text(
        f"{previous_sha} {candidate_sha} {candidate_sha}\n"
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECOVERED_PROMOTION" in result.stdout
    assert f"candidate={candidate_sha}" in result.stdout
    assert _git("rev-parse", "HEAD", cwd=production) == candidate_sha
    assert not (state / "promotion-pending").exists()


def test_weekly_update_is_scheduled_inside_a_hard_resource_boundary() -> None:
    service = WEEKLY_UPDATE_SERVICE.read_text()
    timer = WEEKLY_UPDATE_TIMER.read_text()

    assert "ExecStart=%h/.local/bin/hermes-weekly-update" in service
    assert "MemoryHigh=6G" in service
    assert "MemoryMax=8G" in service
    assert "CPUQuota=600%" in service
    assert "TimeoutStartSec=4h" in service
    assert "OOMPolicy=stop" in service
    assert "OnCalendar=Sun *-*-* 03:15:00 America/New_York" in timer
    assert "Persistent=true" in timer


def test_weekly_updater_matches_upstream_ci_dependency_contract() -> None:
    updater = WEEKLY_UPDATER.read_text()

    required_extras = (
        "all",
        "dev",
        "anthropic",
        "mistral",
        "fal",
        "modal",
        "daytona",
        "hindsight",
        "parallel-web",
    )
    for extra in required_extras:
        assert f"--extra {extra}" in updater
    assert "--all-extras" not in updater
    assert '--project "$repo" --inexact --extra all --locked' in updater


def test_weekly_updater_preserves_each_verification_stage() -> None:
    updater = WEEKLY_UPDATER.read_text()

    for suffix in (
        ".merge-repair.jsonl",
        ".test-repair-${verify_attempt}.jsonl",
        ".verify-${verify_attempt}.log",
        ".candidate-summary.txt",
    ):
        assert suffix in updater
    assert "verify_attempt=1" in updater


def test_weekly_updater_uses_managed_npm_and_real_http_health() -> None:
    updater = WEEKLY_UPDATER.read_text()

    assert 'HERMES_UPDATE_NPM_VERSION:-12.0.2' in updater
    assert "run_verifier_npm ci --no-audit --no-fund" in updater
    assert "http://127.0.0.1:8644/health" in updater
    assert ":9119/api/health" in updater
    assert ":9119/api/status" in updater
    assert '.config_version == .latest_config_version' in updater
    assert 'timeout --signal=TERM --kill-after=30s 5m git -C "$repo" fetch --quiet origin "${HERMES_UPDATE_REF:-main}"' in updater
    assert "for attempt in 1 2 3" in updater
    assert "while ((verify_attempt <= 3))" in updater
    assert "completed 3 bounded verification and repair attempts" in updater
    assert 'repair_pending_file="$state_dir/repair-pending"' in updater
    assert 'HERMES_UPDATE_TARGET_SHA:-' in updater
    assert '"repair" "$candidate" "$candidate_head" "$verify_log"' in updater
    assert updater.count("status --porcelain") == 1
    assert 'if ! worktree_status="$(git -C "$worktree" status --porcelain)"' in updater
    assert "UMask=0077" in updater


def test_weekly_updater_records_marker_before_fast_forward() -> None:
    updater = WEEKLY_UPDATER.read_text()

    marker = 'mv -f -- "$promotion_tmp" "$pending_file"'
    fast_forward = 'git -C "$repo" merge --ff-only "$candidate_sha"'
    clear = 'rm -f -- "$pending_file"'
    assert updater.index(marker) < updater.index(fast_forward)
    assert updater.rindex("verify_production_health") < updater.rindex(clear)


def test_weekly_updater_invalidates_stale_update_status_before_restart() -> None:
    updater = WEEKLY_UPDATER.read_text()

    assert 'rm -f -- "$HOME/.hermes/.update_check"' in updater
    assert 'profiles/*/.update_check' in updater
    assert updater.index("\n  invalidate_update_caches\n") < updater.index(
        "systemctl --user restart hermes-gateway.service hermes-dashboard.service"
    )


def test_weekly_updater_leaves_production_unchanged_when_candidate_fails(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    production_sha = _git("rev-parse", "HEAD", cwd=production)

    (upstream / "version.txt").write_text("broken\n")
    _git("commit", "-am", "candidate", cwd=upstream)

    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(home / "state"),
        "HERMES_UPDATE_VERIFY_COMMAND": "/bin/false",
        "HERMES_UPDATE_REPAIR_COMMAND": "/bin/false",
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "PROMOTED" not in result.stdout
    assert _git("rev-parse", "HEAD", cwd=production) == production_sha
    assert (production / "version.txt").read_text() == "one\n"


def test_weekly_updater_retries_an_interrupted_promotion_before_new_work(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "candidate", cwd=upstream)

    base_env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_VERIFY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    failed = subprocess.run(
        [str(WEEKLY_UPDATER)],
        env={**base_env, "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/false"},
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert (state / "promotion-pending").exists()

    recovered = subprocess.run(
        [str(WEEKLY_UPDATER)],
        env={**base_env, "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true"},
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "RECOVERED_PROMOTION" in recovered.stdout
    assert not (state / "promotion-pending").exists()


def test_weekly_updater_resumes_fixed_candidate_without_target_drift(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)

    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "frozen update", cwd=upstream)
    target_sha = _git("rev-parse", "HEAD", cwd=upstream)

    verify_command = (
        "count=$(test -f repair-count.txt && wc -l < repair-count.txt "
        "|| printf 0); test \"$count\" -ge 4"
    )
    repair_command = (
        "printf 'repair\\n' >> repair-count.txt; "
        "git add repair-count.txt; git commit -m 'test repair'"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_VERIFY_COMMAND": verify_command,
        "HERMES_UPDATE_REPAIR_COMMAND": repair_command,
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }

    first = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )
    assert first.returncode == 75, first.stdout + first.stderr
    assert "REPAIR_PENDING" in first.stdout
    marker = state / "repair-pending"
    assert marker.exists()
    marker_before_check = marker.read_text()
    marker_fields = dict(
        line.split("=", 1) for line in marker_before_check.splitlines()
    )
    assert marker_fields["target"] == target_sha
    candidate = Path(marker_fields["candidate"])
    assert candidate.is_dir()
    assert (candidate / "repair-count.txt").read_text().count("repair") == 3
    candidate_head = _git("rev-parse", "HEAD", cwd=candidate)

    checked = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "REPAIR_PENDING" in checked.stdout
    assert marker.read_text() == marker_before_check
    assert _git("rev-parse", "HEAD", cwd=candidate) == candidate_head

    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "newer update", cwd=upstream)
    newer_sha = _git("rev-parse", "HEAD", cwd=upstream)

    resumed = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "RESUMING_REPAIR" in resumed.stdout
    assert "PROMOTED" in resumed.stdout
    assert f"target={target_sha}" in resumed.stdout
    assert (production / "version.txt").read_text() == "two\n"
    assert (production / "repair-count.txt").read_text().count("repair") == 4
    assert not marker.exists()
    assert not candidate.exists()
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", target_sha, "HEAD"],
            cwd=production,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", newer_sha, "HEAD"],
            cwd=production,
        ).returncode
        != 0
    )


def test_weekly_updater_uses_a_full_fixed_target_without_fetch(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    upstream.mkdir()
    home.mkdir()

    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "fixed update", cwd=upstream)
    target_sha = _git("rev-parse", "HEAD", cwd=upstream)
    _git("fetch", str(upstream), target_sha, cwd=production)
    _git("remote", "set-url", "origin", str(tmp_path / "missing"), cwd=production)

    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(home / "state"),
        "HERMES_UPDATE_TARGET_SHA": target_sha,
        "HERMES_UPDATE_VERIFY_COMMAND": "/bin/true",
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FIXED_TARGET" in result.stdout
    assert f"target={target_sha}" in result.stdout
    assert (production / "version.txt").read_text() == "two\n"


def test_weekly_updater_rejects_an_external_candidate_marker(tmp_path: Path) -> None:
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    production.mkdir()
    state.mkdir(parents=True)
    _git("init", "--initial-branch=main", cwd=production)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (production / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=production)
    _git("commit", "-m", "initial", cwd=production)
    head = _git("rev-parse", "HEAD", cwd=production)
    (state / "repair-pending").write_text(
        "version=1\n"
        f"production={head}\n"
        f"target={head}\n"
        "phase=verify\n"
        f"candidate={tmp_path / 'outside'}\n"
        f"checkpoint={head}\n"
        "failure_log=-\n"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
    }

    result = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "invalid repair candidate path" in result.stderr
    assert _git("rev-parse", "HEAD", cwd=production) == head


def test_weekly_updater_rejects_a_multiline_promotion_marker(
    tmp_path: Path,
) -> None:
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    production.mkdir()
    state.mkdir(parents=True)
    _git("init", "--initial-branch=main", cwd=production)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (production / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=production)
    _git("commit", "-m", "initial", cwd=production)
    head = _git("rev-parse", "HEAD", cwd=production)
    (state / "promotion-pending").write_text(
        f"{head} {head} {head}\nunexpected second line\n"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
    }

    result = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "malformed promotion marker" in result.stderr
    assert (state / "promotion-pending").exists()
    assert _git("rev-parse", "HEAD", cwd=production) == head


def test_weekly_updater_keeps_a_dirty_promotion_candidate(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    unrelated = state / "candidate.UNRELATED"
    upstream.mkdir()
    state.mkdir(parents=True)
    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    previous_sha = _git("rev-parse", "HEAD", cwd=production)
    _git("clone", str(production), str(unrelated), cwd=tmp_path)

    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "candidate", cwd=upstream)
    candidate_sha = _git("rev-parse", "HEAD", cwd=upstream)
    _git("fetch", str(upstream), candidate_sha, cwd=production)
    _git("fetch", str(upstream), candidate_sha, cwd=unrelated)
    _git("checkout", "--detach", candidate_sha, cwd=unrelated)
    (unrelated / "unfinished.txt").write_text("keep me\n")
    (state / "promotion-pending").write_text(
        f"{previous_sha} {candidate_sha} {candidate_sha}\n"
    )
    (state / "repair-pending").write_text(
        "version=1\n"
        f"production={previous_sha}\n"
        f"target={candidate_sha}\n"
        "phase=verify\n"
        f"candidate={unrelated}\n"
        f"checkpoint={candidate_sha}\n"
        "failure_log=-\n"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }

    result = subprocess.run(
        [str(WEEKLY_UPDATER)], env=env, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert unrelated.is_dir()
    assert not (state / "promotion-pending").exists()
    assert not (state / "repair-pending").exists()
    assert len(list(state.glob("repair-pending.invalid.*"))) == 1
    assert _git("rev-parse", "HEAD", cwd=production) == candidate_sha
    assert (unrelated / "unfinished.txt").read_text() == "keep me\n"


def test_weekly_updater_rejects_a_verify_candidate_past_its_checkpoint(
    tmp_path: Path,
) -> None:
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    candidate = state / "candidate.CHANGED"
    production.mkdir()
    state.mkdir(parents=True)
    _git("init", "--initial-branch=main", cwd=production)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (production / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=production)
    _git("commit", "-m", "initial", cwd=production)
    head = _git("rev-parse", "HEAD", cwd=production)
    _git("clone", str(production), str(candidate), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=candidate)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=candidate)
    (state / "repair-pending").write_text(
        "version=1\n"
        f"production={head}\n"
        f"target={head}\n"
        "phase=verify\n"
        f"candidate={candidate}\n"
        f"checkpoint={head}\n"
        "failure_log=-\n"
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
    }

    (candidate / "changed.txt").write_text("unexpected\n")
    dirty = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert dirty.returncode != 0
    assert "dirty during verify phase" in dirty.stderr
    assert (state / "repair-pending").exists()
    assert candidate.is_dir()
    assert _git("rev-parse", "HEAD", cwd=production) == head

    _git("add", "changed.txt", cwd=candidate)
    _git("commit", "-m", "unexpected candidate move", cwd=candidate)
    result = subprocess.run(
        [str(WEEKLY_UPDATER), "--check"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "moved past its recorded checkpoint" in result.stderr
    assert (state / "repair-pending").exists()
    assert candidate.is_dir()
    assert _git("rev-parse", "HEAD", cwd=production) == head


def test_weekly_updater_resumes_an_interrupted_dirty_repair(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    state = home / "state"
    upstream.mkdir()
    home.mkdir()
    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Hermes updater test", cwd=upstream)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    _git("add", "version.txt", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Hermes updater test", cwd=production)
    _git("config", "user.email", "hermes-updater@example.invalid", cwd=production)
    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "candidate", cwd=upstream)

    base_env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(state),
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
    }
    interrupted = subprocess.run(
        [str(WEEKLY_UPDATER)],
        env={
            **base_env,
            "HERMES_UPDATE_VERIFY_COMMAND": "/bin/false",
            "HERMES_UPDATE_REPAIR_COMMAND": (
                "printf 'unfinished\\n' > interrupted.txt; exit 1"
            ),
        },
        capture_output=True,
        text=True,
    )
    assert interrupted.returncode != 0
    marker = state / "repair-pending"
    marker_fields = dict(
        line.split("=", 1) for line in marker.read_text().splitlines()
    )
    assert marker_fields["phase"] == "repair"
    candidate = Path(marker_fields["candidate"])
    assert "interrupted.txt" in _git("status", "--short", cwd=candidate)

    resumed = subprocess.run(
        [str(WEEKLY_UPDATER)],
        env={
            **base_env,
            "HERMES_UPDATE_VERIFY_COMMAND": "/bin/true",
            "HERMES_UPDATE_REPAIR_COMMAND": (
                "git add interrupted.txt; "
                "git commit -m 'complete interrupted repair'"
            ),
        },
        capture_output=True,
        text=True,
    )

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "TEST_REPAIR_RESUMED" in resumed.stdout
    assert "PROMOTED" in resumed.stdout
    assert (production / "interrupted.txt").read_text() == "unfinished\n"
    assert not marker.exists()
    assert not candidate.exists()
