"""Deployment respects partial installations and frozen, explicit update scope."""
import json
import os
from pathlib import Path
import subprocess

import pytest

HELPER = Path(__file__).resolve().parents[1] / "bin/hermes-update-deployment"
SHA = "a" * 40


@pytest.fixture
def deployment(tmp_path):
    repo, home, state, tools = [tmp_path / name for name in ("repo", "home", "state", "tools")]
    for path in (repo, home, state, tools):
        path.mkdir()
    log = tmp_path / "calls"
    for name, script in {
        "systemctl": r'''printf "systemctl %s\n" "$*" >> "$CALLS"
case "$*" in
  *list-units*) printf "%s\n" "${ACTIVE_UNITS:-}";;
  *MainPID*) echo 123;;
  *--property=Environment*)
    [[ "${IDENTITY_MISSING:-0}" != 1 ]] || exit 0
    unit="$3"
    service_home="$HERMES_UPDATE_HOME"
    if [[ "$unit" == hermes-gateway-*.service ]]; then
      name="${unit#hermes-gateway-}"
      service_home+="/profiles/${name%.service}"
    fi
    service_home="${IDENTITY_HOME_OVERRIDE:-$service_home}"
    service_repo="${IDENTITY_REPO_OVERRIDE:-$REPO}"
    printf 'Environment="VIRTUAL_ENV=%s/venv" "HERMES_HOME=%s"\n' "$service_repo" "$service_home"
    printf 'ExecStart={ path=%s/venv/bin/python ; argv[]=python -m hermes_cli.main ; }\n' "$service_repo"
    printf 'WorkingDirectory=%s\n' "$service_home"
    ;;
esac''',
        "uv": 'printf "uv %s\\n" "$*" >> "$CALLS"',
        "npm": 'printf "npm %s\\n" "$*" >> "$CALLS"',
        "node": 'echo v24.0.0',
    }.items():
        path = tools / name
        path.write_text("#!/bin/bash\n" + script + "\n")
        path.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "HERMES_UPDATE_HOME": str(home / ".hermes"),
           "REPO": str(repo), "STATE": str(state), "CALLS": str(log),
           "PATH": str(tools) + ":" + os.environ["PATH"]}
    # Keep operator choices from affecting this fixture.
    for key in list(env):
        if key.startswith("HERMES_UPDATE_") and key != "HERMES_UPDATE_HOME":
            env.pop(key)
    def run(code, **settings):
        return subprocess.run(["bash", "-c", 'set -euo pipefail\nsource "$1"\nrepo="$REPO"\nstate_dir="$STATE"\nprofiles_file="$STATE/scope"\nstate_helper=/nonexistent\nrun_verifier_npm() { npm "$@"; }\n' + code,
                               "test", str(HELPER)], env={**env, **settings}, text=True,
                              capture_output=True, timeout=10)
    return repo, home / ".hermes", state, log, run


def install_python(repo):
    python = repo / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/bash\nprintf "python %s\\n" "$*" >> "$CALLS"\n')
    python.chmod(0o755)
    (repo / "pyproject.toml").write_text('[project]\nname="test"\nversion="1"\n')


def test_minimal_checkout_selects_source_only_without_creating_a_home(deployment):
    repo, home, state, log, run = deployment
    result = run(f'resolve_update_policy\nload_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}\n')
    assert result.returncode == 0, result.stderr
    scope = json.loads((state / "scope").read_text())
    assert set(scope["scope"].values()) == {0}
    assert scope["units"] == []
    assert not home.exists()
    assert not (repo / "venv").exists()
    assert "uv " not in log.read_text()
    assert "restart" not in log.read_text()


def test_cli_dependencies_keep_extras_explicit_and_skip_absent_config(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_RUNTIME="off", HERMES_UPDATE_EXTRAS="anthropic,mistral")
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "--inexact --locked --no-default-groups --extra anthropic --extra mistral" in calls
    assert "--extra all" not in calls
    assert "config migrate" not in calls
    assert "restart" not in calls
    assert not (home / "config.yaml").exists()


def test_only_selected_active_profiles_are_migrated_and_restarted(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    for name in ("work", "personal", "inactive"):
        profile = home / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("{}")
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_RUNTIME="off", HERMES_UPDATE_PROFILES="work,inactive",
                 ACTIVE_UNITS="hermes-gateway-work.service loaded active running\nhermes-gateway-personal.service loaded active running")
    assert result.returncode == 0, result.stderr
    scope = json.loads((state / "scope").read_text())
    assert scope["units"] == ["hermes-gateway-work.service"]
    calls = log.read_text()
    assert "restart hermes-gateway-work.service" in calls
    assert "restart hermes-gateway-personal.service" not in calls
    assert "restart hermes-gateway-inactive.service" not in calls
    assert "hermes-dashboard.service\n" not in calls
    assert calls.count("config migrate") == 2


def test_resume_uses_captured_active_units_even_after_runtime_stop(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    result = run(f'load_deployment_profiles {SHA}', HERMES_UPDATE_RUNTIME="off",
                 ACTIVE_UNITS="hermes-gateway.service loaded active running")
    assert result.returncode == 0, result.stderr
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_RUNTIME="off", ACTIVE_UNITS="")
    assert result.returncode == 0, result.stderr
    assert "restart hermes-gateway.service" in log.read_text()


def test_resume_rejects_changed_policy_before_side_effects(deployment):
    repo, home, state, log, run = deployment
    assert run(f'load_deployment_profiles {SHA}').returncode == 0
    before = log.read_text()
    result = run(f'load_deployment_profiles {SHA}', HERMES_UPDATE_PYTHON="off")
    assert result.returncode == 78
    assert "settings changed" in result.stderr
    assert log.read_text() == before


@pytest.mark.parametrize("setting", ["PYTHON", "NODE", "DASHBOARD", "GATEWAY", "MIGRATE", "RUNTIME"])
def test_explicit_on_requires_an_applicable_installation(deployment, setting):
    repo, home, state, log, run = deployment
    result = run("resolve_update_policy", **{f"HERMES_UPDATE_{setting}": "on"})
    assert result.returncode == 78
    assert "requires an existing applicable installation" in result.stderr
    assert not (state / "scope").exists()


def test_dashboard_requires_node_updates_and_does_not_start_inactive_service(deployment):
    repo, home, state, log, run = deployment
    (repo / "web").mkdir()
    (repo / "package.json").write_text("{}")
    (repo / "web/package.json").write_text("{}")
    result = run("resolve_update_policy", HERMES_UPDATE_DASHBOARD="on", HERMES_UPDATE_NODE="off")
    assert result.returncode == 78
    assert "dashboard builds require node" in result.stderr
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_DASHBOARD="on", HERMES_UPDATE_NODE="on")
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "npm run build --workspace web" in calls
    assert "restart" not in calls


def test_off_actions_do_not_call_runtime_or_migrate_even_when_pending(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    (home / "config.yaml").write_text("{}")
    (state / "runtime-repair").mkdir()
    settings = {f"HERMES_UPDATE_{key}": "off" for key in ("PYTHON", "NODE", "DASHBOARD", "GATEWAY", "MIGRATE", "RUNTIME")}
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}', **settings)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "uv " not in calls and "python " not in calls and "restart" not in calls


def test_gateway_receipt_checks_sha_and_pid_without_requiring_http(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    (home / "gateway_state.json").write_text(json.dumps({"pid": 123, "code_sha": SHA, "gateway_state": "running"}))
    settings = {"HERMES_UPDATE_RUNTIME": "off", "ACTIVE_UNITS": "hermes-gateway.service loaded active running"}
    result = run(f'resolve_update_policy\nverify_profile_code {SHA}', **settings)
    assert result.returncode == 0, result.stderr
    (home / "gateway_state.json").write_text(json.dumps({"pid": 7, "code_sha": SHA}))
    assert run(f'resolve_update_policy\nverify_profile_code {SHA}', **settings).returncode == 1


def test_disabled_installed_dependencies_block_incompatible_source_before_promotion(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("add", "pyproject.toml")
    git("commit", "-qm", "initial")
    previous = git("rev-parse", "HEAD")
    (repo / "pyproject.toml").write_text('[project]\nname="test"\nversion="2"\n')
    git("commit", "-qam", "changed dependencies")
    promoted = git("rev-parse", "HEAD")
    result = run(f'preflight_deployment {previous} {promoted}', HERMES_UPDATE_PYTHON="off", HERMES_UPDATE_RUNTIME="off")
    assert result.returncode == 78
    assert "dependencies changed" in result.stderr


def test_supported_gateway_receipts_require_file_and_complete_identity(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    (repo / "gateway").mkdir()
    (repo / "gateway/status.py").write_text('STATUS="gateway_state.json"\ndef identity(): return {"code_sha": "example"}\n')
    settings = {"HERMES_UPDATE_RUNTIME": "off", "ACTIVE_UNITS": "hermes-gateway.service loaded active running"}
    assert run(f'resolve_update_policy\nverify_profile_code {SHA}', **settings).returncode == 1
    (home / "gateway_state.json").write_text(json.dumps({"pid": 123, "gateway_state": "running"}))
    assert run(f'resolve_update_policy\nverify_profile_code {SHA}', **settings).returncode == 1
    (home / "gateway_state.json").write_text(json.dumps({"pid": 123, "gateway_state": "running", "code_sha": SHA}))
    assert run(f'resolve_update_policy\nverify_profile_code {SHA}', **settings).returncode == 0


def test_runtime_pending_with_excluded_service_blocks_in_preflight(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    (state / "runtime-repair").mkdir()
    result = run(f'preflight_deployment {SHA} {SHA}', HERMES_UPDATE_PYTHON="off",
                 HERMES_UPDATE_GATEWAY="off", ACTIVE_UNITS="hermes-gateway.service loaded active running")
    assert result.returncode == 78
    assert "runtime repair requires maintenance of all active" in result.stderr
    assert "stop " not in log.read_text()
    assert not (state / "scope").exists()


def test_auto_detects_current_dashboard_build_directory(deployment):
    repo, home, state, log, run = deployment
    (repo / "hermes_cli/web_dist").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    result = run(f'load_deployment_profiles {SHA}')
    assert result.returncode == 0, result.stderr
    snapshot = json.loads((state / "scope").read_text())
    assert snapshot["scope"]["dashboard"] == 1
    assert snapshot["dashboard_active"] == 0


def test_installed_dashboard_selects_node_dependencies_after_modules_cleanup(deployment):
    repo, home, state, log, run = deployment
    (repo / "hermes_cli/web_dist").mkdir(parents=True)
    result = run(f'load_deployment_profiles {SHA}')
    assert result.returncode == 0, result.stderr
    snapshot = json.loads((state / "scope").read_text())
    assert snapshot["scope"]["node"] == snapshot["scope"]["dashboard"] == 1


def test_legacy_pending_inventory_migrates_only_when_transaction_is_loaded(deployment):
    repo, home, state, log, run = deployment
    original = f"{SHA}\nhermes-gateway.service\nhermes-gateway-work.service\n"
    (state / "scope").write_text(original)
    result = run('resolve_update_policy\nprintf "%s:%s:%s" "$update_python" "$update_runtime" "$dashboard_was_active"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1:1:1"
    assert (state / "scope").read_text() == original
    assert not (state / "scope.legacy").exists()
    assert not log.exists()  # Frozen units survive being stopped by the old updater.
    result = run(f'load_deployment_profiles {SHA}')
    assert result.returncode == 0, result.stderr
    snapshot = json.loads((state / "scope").read_text())
    assert set(snapshot["scope"].values()) == {1}
    assert snapshot["dashboard_active"] == 1
    assert snapshot["units"] == ["hermes-gateway.service", "hermes-gateway-work.service"]
    assert snapshot["homes"] == [str(home), str(home / "profiles/work")]
    assert (state / "scope.legacy").read_text() == original
    assert run(f'load_deployment_profiles {SHA}').returncode == 0


@pytest.mark.parametrize("setting,value", [("HERMES_UPDATE_GATEWAY", "off"), ("HERMES_UPDATE_EXTRAS", "all"), ("HERMES_UPDATE_HOME", "/different/hermes")])
def test_legacy_resume_rejects_incompatible_settings_without_rewriting(deployment, setting, value):
    repo, home, state, log, run = deployment
    original = f"{SHA}\nhermes-gateway.service\n"
    (state / "scope").write_text(original)
    result = run(f'load_deployment_profiles {SHA}', **{setting: value})
    assert result.returncode == 78
    assert "unfinished legacy update requires" in result.stderr
    assert (state / "scope").read_text() == original
    assert not (state / "scope.legacy").exists()


def test_legacy_resume_rejects_wrong_previous_sha_without_rewriting(deployment):
    repo, home, state, log, run = deployment
    original = f"{SHA}\nhermes-gateway.service\n"
    (state / "scope").write_text(original)
    result = run(f'load_deployment_profiles {"b" * 40}')
    assert result.returncode == 78
    assert "inventory does not match previous source" in result.stderr
    assert (state / "scope").read_text() == original
    assert not (state / "scope.legacy").exists()


def test_unrelated_live_services_do_not_affect_a_custom_source_only_checkout(deployment):
    repo, home, state, log, run = deployment
    custom_home = home.parent / "custom hermes"
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_HOME=str(custom_home), IDENTITY_REPO_OVERRIDE="/another/hermes-agent",
                 IDENTITY_HOME_OVERRIDE="/another/home",
                 ACTIVE_UNITS="hermes-gateway.service loaded active running\nhermes-dashboard.service loaded active running")
    assert result.returncode == 0, result.stderr
    snapshot = json.loads((state / "scope").read_text())
    assert set(snapshot["scope"].values()) == {0}
    assert snapshot["active_units"] == []
    assert snapshot["dashboard_shared"] == 0
    assert "restart" not in log.read_text()
    assert not custom_home.exists()


def test_custom_home_with_proven_service_identity_is_selected(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    custom_home = home.parent / "custom hermes"
    custom_home.mkdir()
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}',
                 HERMES_UPDATE_HOME=str(custom_home), HERMES_UPDATE_RUNTIME="off",
                 ACTIVE_UNITS="hermes-gateway.service loaded active running")
    assert result.returncode == 0, result.stderr
    assert json.loads((state / "scope").read_text())["units"] == ["hermes-gateway.service"]
    assert "restart hermes-gateway.service" in log.read_text()


@pytest.mark.parametrize("unit", ["hermes-gateway.service", "hermes-dashboard.service"])
def test_services_sharing_checkout_with_other_home_block_runtime_swap(deployment, unit):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    (state / "runtime-repair").mkdir()
    result = run(f'preflight_deployment {SHA} {SHA}', HERMES_UPDATE_PYTHON="off",
                 IDENTITY_HOME_OVERRIDE="/another/home", ACTIVE_UNITS=f"{unit} loaded active running")
    assert result.returncode == 78
    assert "runtime repair requires maintenance of all active" in result.stderr
    assert "restart" not in log.read_text()
    assert " stop " not in log.read_text()


def test_ambiguous_active_service_identity_blocks_before_transaction(deployment):
    repo, home, state, log, run = deployment
    result = run(f'load_deployment_profiles {SHA}', IDENTITY_MISSING="1",
                 ACTIVE_UNITS="hermes-gateway.service loaded active running")
    assert result.returncode == 78
    assert "cannot establish active service identity" in result.stderr
    assert not (state / "scope").exists()


def test_shared_dashboard_inventory_survives_resume_without_managing_it(deployment):
    repo, home, state, log, run = deployment
    install_python(repo)
    home.mkdir()
    settings = {"HERMES_UPDATE_RUNTIME": "off", "IDENTITY_HOME_OVERRIDE": "/another/home"}
    result = run(f'load_deployment_profiles {SHA}', ACTIVE_UNITS="hermes-dashboard.service loaded active running", **settings)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads((state / "scope").read_text())
    assert snapshot["dashboard_shared"] == 1 and snapshot["dashboard_active"] == 0
    result = run(f'load_deployment_profiles {SHA}\ndeploy_production {SHA} {SHA}', ACTIVE_UNITS="", **settings)
    assert result.returncode == 0, result.stderr
    assert "restart hermes-dashboard.service" not in log.read_text()
