"""Exercise unattended updater failures with local repositories and fake tools."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from test_hermes_reliability import WEEKLY_UPDATER, _git


def _update_case(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    upstream = tmp_path / "upstream"
    production = tmp_path / "production"
    home = tmp_path / "home"
    upstream.mkdir()
    home.mkdir()
    _git("init", "--initial-branch=main", cwd=upstream)
    _git("config", "user.name", "Updater test", cwd=upstream)
    _git("config", "user.email", "updater@example.invalid", cwd=upstream)
    (upstream / "version.txt").write_text("one\n")
    (upstream / ".gitignore").write_text("venv/\nnode_modules/\n")
    _git("add", "version.txt", ".gitignore", cwd=upstream)
    _git("commit", "-m", "initial", cwd=upstream)
    _git("clone", str(upstream), str(production), cwd=tmp_path)
    _git("config", "user.name", "Updater test", cwd=production)
    _git("config", "user.email", "updater@example.invalid", cwd=production)
    (upstream / "version.txt").write_text("two\n")
    _git("commit", "-am", "upstream update", cwd=upstream)
    env = {
        **os.environ,
        "HOME": str(home),
        "HERMES_UPDATE_REPO": str(production),
        "HERMES_UPDATE_STATE_DIR": str(home / "state"),
        "HERMES_UPDATE_VERIFY_COMMAND": "/bin/true",
        "HERMES_UPDATE_DEPLOY_COMMAND": "/bin/true",
        "HERMES_UPDATE_HEALTH_COMMAND": "/bin/true",
        "HERMES_UPDATE_REPAIR_COMMAND": "/bin/false",
    }
    # Do not inherit a target or a repair budget from the operator's shell.
    env.pop("HERMES_UPDATE_TARGET_SHA", None)
    env.pop("HERMES_UPDATE_MAX_REPAIRS", None)
    env.pop("HERMES_UPDATE_REF", None)
    return upstream, production, home, env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WEEKLY_UPDATER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _pending(home: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (home / "state" / "repair-pending").read_text().splitlines()
    )


@pytest.mark.parametrize("commit_change", [False, True], ids=["dirty", "new-head"])
def test_successful_verifier_cannot_change_the_verified_candidate(
    tmp_path: Path, commit_change: bool
) -> None:
    _, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    command = "printf 'untested change\\n' > version.txt"
    if commit_change:
        command += "; git add version.txt; git commit -m 'changed during verification'"
    env["HERMES_UPDATE_VERIFY_COMMAND"] = command

    result = _run(env)

    assert result.returncode == 78, result.stdout + result.stderr
    assert "PROMOTED" not in result.stdout
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert (production / "version.txt").read_text() == "one\n"
    candidate = Path(_pending(home)["candidate"])
    assert candidate.is_dir()
    assert (candidate / "version.txt").read_text() == "untested change\n"
    assert not (home / "state" / "promotion-pending").exists()
    marker_before = (home / "state" / "repair-pending").read_text()
    repeated = _run(env)
    assert repeated.returncode == 78, repeated.stdout + repeated.stderr
    assert (home / "state" / "repair-pending").read_text() == marker_before
    assert _git("rev-parse", "HEAD", cwd=production) == previous


def _python_tool(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("bad_state", "shared_only"),
    [("", False), ("sha", False), ("pid", False), ("", True)],
    ids=["recover", "wrong-sha", "wrong-pid", "shared-source-build"],
)
def test_profile_snapshot_survives_failed_restart_and_requires_current_process(
    tmp_path: Path, bad_state: str, shared_only: bool
) -> None:
    upstream, production, home, env = _update_case(tmp_path)
    if shared_only:
        (upstream / "version.txt").write_text("one\n")
        shared_file = upstream / "apps" / "shared" / "component.ts"
        shared_file.parent.mkdir(parents=True)
        shared_file.write_text("export const version = 2;\n")
        _git("add", "version.txt", "apps/shared/component.ts", cwd=upstream)
        _git("commit", "-m", "shared application source only", cwd=upstream)
    previous = _git("rev-parse", "HEAD", cwd=production)
    profile = home / ".hermes" / "profiles" / "alpha"
    profile.mkdir(parents=True)
    for profile_home in (home / ".hermes", profile):
        (profile_home / ".update_check").write_text("stale")
    control = home / "control"
    control.mkdir()
    (control / "fail-next-profile").touch()
    fake_bin = home / "bin"
    _python_tool(
        fake_bin / "systemctl",
        """
        import json, os, subprocess, sys
        from pathlib import Path
        control = Path(os.environ['FAKE_CONTROL'])
        args = sys.argv[1:]
        if args[0] == '--user': args = args[1:]
        with (control / 'calls').open('a') as log:
            log.write(' '.join(args) + '\\n')
        command = args[0]
        if command == 'list-units':
            if not (control / 'inactive').exists():
                print('hermes-gateway-alpha.service loaded active running Alpha')
        elif command == 'restart':
            for unit in args[1:]:
                if unit == 'hermes-gateway-alpha.service' and (control / 'fail-next-profile').exists():
                    (control / 'fail-next-profile').unlink()
                    (control / 'inactive').touch()
                    sys.exit(1)
                if not unit.startswith('hermes-gateway'): continue
                is_profile = unit == 'hermes-gateway-alpha.service'
                sha = subprocess.check_output(['git', '-C', os.environ['HERMES_UPDATE_REPO'], 'rev-parse', 'HEAD'], text=True).strip()
                pid = 222 if is_profile else 111
                if is_profile and os.environ.get('FAKE_BAD_STATE') == 'sha': sha = '0' * 40
                if is_profile and os.environ.get('FAKE_BAD_STATE') == 'pid': pid = 999
                path = Path(os.environ['HOME']) / '.hermes'
                if is_profile: path = path / 'profiles' / 'alpha'
                (path / 'gateway_state.json').write_text(json.dumps({'code_sha': sha, 'pid': pid, 'gateway_state': 'running'}))
                if is_profile: (control / 'inactive').unlink(missing_ok=True)
        elif command == 'is-active':
            if 'hermes-gateway-alpha.service' in args and (control / 'inactive').exists(): sys.exit(3)
        elif command == 'show':
            print('222' if 'hermes-gateway-alpha.service' in args else '111')
        else:
            sys.exit(90)
        """,
    )
    _python_tool(
        fake_bin / "curl",
        """
        import json
        print(json.dumps({'status': 'ok', 'ok': True, 'gateway_running': True,
                         'gateway_state': 'running', 'config_version': 1,
                         'latest_config_version': 1,
                         'components': {name: {'status': 'ok'} for name in ('gateway', 'dashboard', 'storage')}}))
        """,
    )
    _python_tool(fake_bin / "sleep", "pass\n")
    _python_tool(
        production / "venv" / "bin" / "python",
        """
        import os
        from pathlib import Path
        with (Path(os.environ['FAKE_CONTROL']) / 'migrations').open('a') as log:
            log.write(os.environ['HERMES_HOME'] + '\\n')
        """,
    )
    env.pop("HERMES_UPDATE_DEPLOY_COMMAND")
    env.pop("HERMES_UPDATE_HEALTH_COMMAND")
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    env["FAKE_CONTROL"] = str(control)
    if shared_only:
        events = _default_verifier_tools(home, env)
        env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/true"

    failed = _run(env)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    snapshot = home / "state" / "deployment-profiles"
    assert snapshot.read_text().splitlines() == [
        previous, "hermes-gateway.service", "hermes-gateway-alpha.service"
    ]
    assert (home / "state" / "promotion-pending").exists()
    assert (control / "inactive").exists()
    assert not (profile / ".update_check").exists()
    assert not (home / ".hermes" / ".update_check").exists()

    env["FAKE_BAD_STATE"] = bad_state
    resumed = _run(env)
    if bad_state:
        assert resumed.returncode != 0, resumed.stdout + resumed.stderr
        assert snapshot.exists()
        assert (home / "state" / "promotion-pending").exists()
        env["FAKE_BAD_STATE"] = ""
        resumed = _run(env)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "RECOVERED_PROMOTION" in resumed.stdout
    calls = (control / "calls").read_text().splitlines()
    assert sum(call.startswith("list-units ") for call in calls) == 1
    assert calls.count("restart hermes-gateway-alpha.service") >= 2
    assert "show hermes-gateway-alpha.service -p MainPID --value" in calls
    assert str(profile) in (control / "migrations").read_text().splitlines()
    assert not snapshot.exists()
    assert not (home / "state" / "promotion-pending").exists()
    if shared_only:
        assert _git("diff", "--name-only", previous, "HEAD", cwd=production) == "apps/shared/component.ts"
        assert events.read_text().splitlines() == ["npm run build --workspace web"] * 2


def _default_verifier_tools(home: Path, env: dict[str, str]) -> Path:
    events = home / "gate-events"
    fake_bin = home / "bin"
    _python_tool(
        home / "fake-test-python",
        f"""
        import sys
        from pathlib import Path
        if '--version' in sys.argv:
            print('Python 3.11.0')
            sys.exit(0)
        with Path({str(events)!r}).open('a') as log: log.write('python-gate\\n')
        """,
    )
    _python_tool(
        fake_bin / "uv",
        f"""
        import os, shutil, sys
        from pathlib import Path
        if '--version' in sys.argv:
            print('uv 0.8.0')
        else:
            with Path({str(events)!r}).open('a') as log: log.write('python-deps\\n')
            target = Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin' / 'python'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2({str(home / 'fake-test-python')!r}, target)
        """,
    )
    # Dependency-cache fixtures live under pytest's /tmp. Namespace behavior
    # has a separate real-bubblewrap test; these fixtures fake external tools.
    _python_tool(fake_bin / "bwrap", "import os, sys; args = sys.argv[sys.argv.index('--') + 1:]; os.execvp(args[0], args)\n")
    _python_tool(fake_bin / "node", "print('v24.0.0')\n")
    npm = home / "state" / "toolchain" / "npm-12.0.2" / "node_modules" / ".bin" / "npm"
    _python_tool(
        npm,
        f"""
        import sys
        from pathlib import Path
        args = sys.argv[1:]
        if args == ['--version']:
            print('12.0.2')
            sys.exit(0)
        with Path({str(events)!r}).open('a') as log: log.write('npm ' + ' '.join(args) + '\\n')
        if args[0] == 'ci': Path('node_modules').mkdir(exist_ok=True)
        if args[:2] == ['run', 'check']:
            flag = Path({str(home / 'fail-npm-check')!r})
            if flag.exists():
                status = int(flag.read_text().strip() or '69')
                flag.unlink()
                sys.exit(status)
        """,
    )
    env.pop("HERMES_UPDATE_VERIFY_COMMAND")
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    return events


@pytest.mark.parametrize("remove_venv", [False, True], ids=["reuse", "rebuild-deleted-venv"])
def test_transient_node_gate_failure_reuses_verified_python_and_dependencies(
    tmp_path: Path, remove_venv: bool
) -> None:
    upstream, production, home, env = _update_case(tmp_path)
    target = _git("rev-parse", "HEAD", cwd=upstream)
    previous = _git("rev-parse", "HEAD", cwd=production)
    events = _default_verifier_tools(home, env)
    (home / "fail-npm-check").touch()

    failed = _run(env)
    assert failed.returncode == 69, failed.stdout + failed.stderr
    marker = _pending(home)
    checkpoint = marker["checkpoint"]
    assert marker["target"] == target
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert not (Path(marker["candidate"]) / ".git" / "hermes-repair-count").exists()
    if remove_venv:
        shutil.rmtree(Path(marker["candidate"]) / "venv")

    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "later target", cwd=upstream)
    _git("remote", "set-url", "origin", str(tmp_path / "missing"), cwd=production)
    resumed = _run(env)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _git("rev-parse", "HEAD", cwd=production) == checkpoint
    calls = events.read_text().splitlines()
    assert calls.count("python-deps") == (2 if remove_venv else 1)
    assert calls.count("python-gate") == (2 if remove_venv else 1)
    assert calls.count("npm ci --no-audit --no-fund") == 1
    assert calls.count("npm run check") == 2
    assert calls.count("npm run build --workspace web") == 1
    assert not (home / "state" / "repair-pending").exists()


def test_failed_npm_bootstrap_cannot_run_an_old_npm_binary(tmp_path: Path) -> None:
    _, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    _default_verifier_tools(home, env)
    wrong_npm_calls = home / "wrong-npm-calls"
    npm = home / "state" / "toolchain" / "npm-12.0.2" / "node_modules" / ".bin" / "npm"
    _python_tool(
        npm,
        f"""
        import sys
        from pathlib import Path
        if sys.argv[1:] == ['--version']:
            print('11.0.0')
        else:
            Path({str(wrong_npm_calls)!r}).touch()
        """,
    )
    _python_tool(home / "bin" / "npm", "import sys\nsys.exit(42)\n")

    failed = _run(env)

    assert failed.returncode == 69, failed.stdout + failed.stderr
    assert not wrong_npm_calls.exists()
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert Path(_pending(home)["candidate"]).is_dir()


def test_dependency_preparation_failure_never_starts_source_repair(
    tmp_path: Path,
) -> None:
    upstream, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    target = _git("rev-parse", "HEAD", cwd=upstream)
    fake_bin = home / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nprintf 'package download failed\\n' >&2\nexit 42\n")
    fake_uv.chmod(0o755)
    repair_calls = home / "repair-calls"
    env.pop("HERMES_UPDATE_VERIFY_COMMAND")
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    env["REPAIR_CALLS"] = str(repair_calls)
    env["HERMES_UPDATE_REPAIR_COMMAND"] = 'printf "repair\\n" >> "$REPAIR_CALLS"'

    failed = _run(env)

    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert not repair_calls.exists()
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    marker = _pending(home)
    assert marker["target"] == target
    candidate = Path(marker["candidate"])
    assert candidate.is_dir()

    # A service retry must use the same target even when upstream has advanced.
    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "later upstream update", cwd=upstream)
    _git("remote", "set-url", "origin", str(tmp_path / "missing"), cwd=production)
    retried = _run(env)
    assert retried.returncode != 0, retried.stdout + retried.stderr
    assert not repair_calls.exists()
    assert _pending(home)["target"] == target
    assert _pending(home)["candidate"] == str(candidate)
    assert _git("rev-parse", "HEAD", cwd=production) == previous


def test_total_repair_budget_survives_service_restarts(tmp_path: Path) -> None:
    upstream, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    target = _git("rev-parse", "HEAD", cwd=upstream)
    repair_calls = home / "repair-calls"
    env.update(
        {
            "HERMES_UPDATE_MAX_REPAIRS": "4",
            "HERMES_UPDATE_VERIFY_COMMAND": "/bin/false",
            "REPAIR_CALLS": str(repair_calls),
            "HERMES_UPDATE_REPAIR_COMMAND": (
                'printf "repair\\n" >> "$REPAIR_CALLS"; '
                "printf 'repair\\n' >> repair.txt; "
                "git add repair.txt; git commit -m 'focused repair'"
            ),
        }
    )

    results = []
    for _ in range(6):
        result = _run(env)
        results.append(result)
        assert len(repair_calls.read_text().splitlines()) <= 4
        if result.returncode == 78:
            break
        assert result.returncode == 75, result.stdout + result.stderr

    assert results[-1].returncode == 78, results[-1].stdout + results[-1].stderr
    assert len(results) >= 2, "The service must bound work within each run."
    assert repair_calls.read_text().splitlines() == ["repair"] * 4
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert _pending(home)["target"] == target
    assert Path(_pending(home)["candidate"]).is_dir()

    # A later service start cannot reset an exhausted transaction's budget.
    repeated = _run(env)
    assert repeated.returncode == 78, repeated.stdout + repeated.stderr
    assert repair_calls.read_text().splitlines() == ["repair"] * 4
    assert _git("rev-parse", "HEAD", cwd=production) == previous


def test_successful_repair_with_no_commit_stops_the_transaction(
    tmp_path: Path,
) -> None:
    _, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/false"
    env["HERMES_UPDATE_REPAIR_COMMAND"] = "/bin/true"

    failed = _run(env)

    assert failed.returncode == 78, failed.stdout + failed.stderr
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert Path(_pending(home)["candidate"]).is_dir()

    # The terminal result must survive a service restart without another repair.
    env["REPAIR_CALLS"] = str(home / "unexpected-repair")
    env["HERMES_UPDATE_REPAIR_COMMAND"] = 'printf "repair\\n" >> "$REPAIR_CALLS"'
    repeated = _run(env)
    assert repeated.returncode == 78, repeated.stdout + repeated.stderr
    assert not (home / "unexpected-repair").exists()
    assert _git("rev-parse", "HEAD", cwd=production) == previous


def test_annotated_target_tag_freezes_the_commit_instead_of_the_tag_object(
    tmp_path: Path,
) -> None:
    upstream, production, home, env = _update_case(tmp_path)
    target = _git("rev-parse", "HEAD", cwd=upstream)
    _git("tag", "-a", "candidate-release", "-m", "fixed candidate", cwd=upstream)
    tag_object = _git("rev-parse", "refs/tags/candidate-release", cwd=upstream)
    assert tag_object != target
    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "newer main commit", cwd=upstream)
    env["HERMES_UPDATE_REF"] = "refs/tags/candidate-release"
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "exit 69"

    failed = _run(env)

    assert failed.returncode == 69, failed.stdout + failed.stderr
    assert _pending(home)["target"] == target
    assert _pending(home)["target"] != tag_object
    _git("remote", "set-url", "origin", str(tmp_path / "missing"), cwd=production)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/true"
    recovered = _run(env)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert f"target={target}" in recovered.stdout
    assert (production / "version.txt").read_text() == "two\n"


def test_source_only_repair_reruns_tests_without_reinstalling_dependencies(
    tmp_path: Path,
) -> None:
    _, production, home, env = _update_case(tmp_path)
    events = _default_verifier_tools(home, env)
    (home / "fail-npm-check").write_text("1")
    env["HERMES_UPDATE_REPAIR_COMMAND"] = (
        "printf 'fixed source\\n' > version.txt; "
        "git add version.txt; git commit -m 'fix source regression'"
    )

    result = _run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (production / "version.txt").read_text() == "fixed source\n"
    calls = events.read_text().splitlines()
    assert calls.count("python-deps") == 1
    assert calls.count("npm ci --no-audit --no-fund") == 1
    assert calls.count("python-gate") == 2
    assert calls.count("npm run check") == 2
    assert calls.count("npm run build --workspace web") == 1
    assert not (home / "state" / "repair-pending").exists()
