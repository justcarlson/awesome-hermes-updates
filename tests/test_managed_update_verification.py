"""Isolated checks for the read-only completed-update verifier."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).with_name("verify_managed_update.py")
SPEC = importlib.util.spec_from_file_location("verify_managed_update", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

TARGET = "a" * 40
HEAD = "b" * 40


def run_verifier(monkeypatch, tmp_path, *, service=None, pending=(), ancestor=True,
                 gateway_pid=172, state_pid=172, state_sha=HEAD, health=None,
                  status=None, version=None, runtime="safe=True", baseline=None,
                  dashboard_pid=333, platform_states=None, omit_baseline=False):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    home = tmp_path / "home"
    output = tmp_path / "report.json"
    (repo / "venv/bin").mkdir(parents=True)
    state.mkdir()
    home.mkdir()
    (state / "last-result").write_text(f"exit_status=0\ntarget={TARGET}\n")
    for name in pending:
        (state / name).touch()
    platform_states = {"healthy": "connected"} if platform_states is None else platform_states
    gateway_platforms = {name: {"state": value} for name, value in platform_states.items()}
    (home / "gateway_state.json").write_text(json.dumps({
        "pid": state_pid,
        "gateway_state": "running",
        "code_sha": state_sha,
        "platforms": gateway_platforms,
    }))
    service = service if service is not None else {
        "ActiveState": "inactive", "SubState": "dead", "Result": "success",
        "ExecMainStatus": "0", "MemoryPeak": "1", "CPUUsageNSec": "1",
    }
    health = health if health is not None else {"ok": True, "version": "1.2.3"}
    status = status if status is not None else {"components": {
        "gateway": {"status": "ok"}, "dashboard": {"status": "ok"},
        "storage": {"status": "ok"},
    }, "gateway_platforms": gateway_platforms}
    version = version or f"Hermes Agent v{health['version']} · upstream {TARGET[:8]} · local {HEAD[:8]}"

    def command(*arguments, cwd=None):
        if arguments[:4] == ("systemctl", "--user", "show", "hermes-weekly-update.service"):
            return "\n".join(f"{key}={value}" for key, value in service.items())
        if arguments[:4] == ("systemctl", "--user", "show", "hermes-gateway.service"):
            return str(gateway_pid)
        if arguments[:4] == ("systemctl", "--user", "show", "hermes-dashboard.service"):
            return str(dashboard_pid)
        if arguments[:4] == ("git", "-C", str(repo), "rev-parse"):
            return HEAD
        if arguments[:4] == ("git", "-C", str(repo), "status"):
            return ""
        if arguments[:3] == ("systemctl", "--user", "is-active"):
            return "active"
        if arguments[:3] == ("systemctl", "--user", "is-enabled"):
            return "enabled"
        if arguments[0] == str(repo / "venv/bin/python"):
            return version
        if arguments[0] == verifier.sys.executable:
            return runtime
        raise AssertionError(f"unexpected command: {arguments}")

    def urlopen(url, timeout):
        body = health if url.endswith("/api/health") else status
        return io.StringIO(json.dumps(body))

    monkeypatch.setattr(verifier, "command", command)
    monkeypatch.setattr(verifier, "urlopen", urlopen)
    monkeypatch.setattr(
        verifier.subprocess, "run",
        lambda *_, **__: SimpleNamespace(returncode=0 if ancestor else 1),
    )
    arguments = ["verify-managed-update", "--repo", str(repo), "--home", str(home),
                 "--state", str(state), "--target", TARGET,
                 "--dashboard-url", "http://dashboard", "--profile", "default",
                 "--output", str(output)]
    if not omit_baseline:
        if baseline is None:
            baseline = {'details': {'installed_sha': 'd' * 40, 'dashboard': {'pid': 111},
                                   'gateways': {'default': {'platform_states': {'healthy': 'connected'}}}}}
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))
        arguments.extend(("--baseline-report", str(baseline_path)))
    monkeypatch.setattr(verifier.sys, "argv", arguments)
    return verifier.main(), json.loads(output.read_text())


def test_successful_completed_update_passes_all_checks(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path)
    assert result == 0
    assert all(report["checks"].values())


def test_activating_service_is_not_a_completed_success(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path, service={
        "ActiveState": "activating", "Result": "success", "ExecMainStatus": "0",
    })
    assert result == 1
    assert report["checks"]["terminal_service_success"] is False


@pytest.mark.parametrize("marker", ["repair-pending", "promotion-pending"])
def test_pending_transaction_marker_fails_verification(monkeypatch, tmp_path, marker):
    result, report = run_verifier(monkeypatch, tmp_path, pending=(marker,))
    assert result == 1
    assert report["checks"]["no_pending_state"] is False


def test_target_not_reachable_from_installed_head_fails(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path, ancestor=False)
    assert result == 1
    assert report["checks"]["target_in_production"] is False


@pytest.mark.parametrize(("gateway_pid", "state_pid", "state_sha"), [
    (172, 999, HEAD),
    (172, 172, "c" * 40),
])
def test_gateway_pid_or_code_sha_mismatch_fails(monkeypatch, tmp_path, gateway_pid, state_pid, state_sha):
    result, report = run_verifier(
        monkeypatch, tmp_path, gateway_pid=gateway_pid, state_pid=state_pid, state_sha=state_sha)
    assert result == 1
    assert report["checks"]["gateway_default"] is False


def test_stale_successful_receipt_for_other_target_fails(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path)
    assert result == 0
    # Re-run with a stale receipt while retaining every other healthy observation.
    state = tmp_path / "state"
    (state / "last-result").write_text("exit_status=0\ntarget=" + "d" * 40 + "\n")
    monkeypatch.setattr(verifier.sys, "argv", [
        "verify-managed-update", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home"),
        "--state", str(state), "--target", TARGET, "--dashboard-url", "http://dashboard",
        "--profile", "default", "--output", str(tmp_path / "stale-report.json"),
        "--baseline-report", str(tmp_path / 'baseline.json'),
    ])
    assert verifier.main() == 1
    assert json.loads((tmp_path / "stale-report.json").read_text())["checks"]["successful_target_receipt"] is False


def test_runtime_version_and_dashboard_health_are_required(monkeypatch, tmp_path):
    result, report = run_verifier(
        monkeypatch, tmp_path, health={"ok": False, "version": "1.2.3"},
        version="Hermes Agent v1.2.3 (deadbeef)", runtime="safe=False")
    assert result == 1
    assert report["checks"]["dashboard_http_health"] is False
    assert report["checks"]["version_matches"] is False
    assert report["checks"]["safe_sqlite_runtime"] is False


def test_changed_source_requires_dashboard_restart_and_preserves_connections(monkeypatch, tmp_path):
    baseline = {"details": {
        "installed_sha": "d" * 40,
        "dashboard": {"pid": 333},
        "gateways": {"default": {"platform_states": {"healthy": "connected"}}},
    }}
    result, report = run_verifier(monkeypatch, tmp_path, baseline=baseline)
    assert result == 1
    assert report["checks"]["dashboard_restarted"] is False
    assert report["checks"]["connections_preserved_default"] is True


def test_preexisting_disconnected_adapter_is_reported_but_not_a_health_failure(monkeypatch, tmp_path):
    baseline = {"details": {
        "installed_sha": "d" * 40,
        "dashboard": {"pid": 111},
        "gateways": {"default": {"platform_states": {"offline": "disconnected"}}},
    }}
    result, report = run_verifier(
        monkeypatch, tmp_path, baseline=baseline, platform_states={"offline": "disconnected"})
    assert result == 0
    assert report["details"]["gateways"]["default"]["platform_states"] == {"offline": "disconnected"}
    assert report["checks"]["connections_preserved_default"] is True


@pytest.mark.parametrize('states', [{}, {'healthy': 'retrying'}])
def test_lost_existing_connection_fails(monkeypatch, tmp_path, states):
    baseline = {'details': {
        'installed_sha': HEAD, 'dashboard': {'pid': 333},
        'gateways': {'default': {'platform_states': {'healthy': 'connected'}}},
    }}
    result, report = run_verifier(monkeypatch, tmp_path, baseline=baseline, platform_states=states)
    assert result == 1
    assert report['checks']['connections_preserved_default'] is False


def test_missing_service_properties_produce_a_failed_report(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path, service={})
    assert result == 1
    assert report['checks']['observation_completed'] is False


def test_malformed_receipt_replaces_old_success_report(monkeypatch, tmp_path):
    assert run_verifier(monkeypatch, tmp_path)[0] == 0
    (tmp_path / 'state/last-result').write_text('truncated receipt\n')
    assert verifier.main() == 1
    report = json.loads((tmp_path / 'report.json').read_text())
    assert report['checks']['observation_completed'] is False


def test_missing_baseline_cannot_claim_complete_update_proof(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path, omit_baseline=True,
                                 platform_states={'offline': 'disconnected'})
    assert result == 1
    assert report['checks']['baseline_available'] is False


def test_upstream_sha_cannot_substitute_for_installed_version_sha(monkeypatch, tmp_path):
    result, report = run_verifier(monkeypatch, tmp_path,
                                 version=f'Hermes Agent v1.2.3 · upstream {HEAD[:8]} · local deadbeef')
    assert result == 1
    assert report['checks']['version_matches'] is False
