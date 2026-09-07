"""Fault tests for durable per-file evidence and automatic transaction recovery."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_hermes_reliability import _git, WEEKLY_UPDATER
from test_hermes_updater_unattended import _update_case, _run, _pending

HELPER = WEEKLY_UPDATER.with_name("hermes-update-state")
loader = importlib.machinery.SourceFileLoader("hermes_update_evidence", str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
evidence = importlib.util.module_from_spec(spec)
loader.exec_module(evidence)


def commit(repo):
    _git("add", "--", "tests", "scripts", "runtime.py", ".gitignore", cwd=repo)
    _git("commit", "-m", "fixture", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


def test_generated_database_artifacts_are_preserved_without_promotion_block(tmp_path):
    _, production, home, env = _update_case(tmp_path)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "mkdir -p MagicMock; printf 'SQLite format 3\\000payload' > MagicMock/123; touch MagicMock/123.lock"
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TEST_ARTIFACTS_ARCHIVED count=2" in result.stdout
    assert len(list((home / "state/test-artifacts").glob("*/MagicMock/123"))) == 1
    assert _git("status", "--short", cwd=production) == ""
    assert not (production / "MagicMock").exists()


def test_untracked_python_source_is_never_treated_as_test_output(tmp_path):
    _, production, home, env = _update_case(tmp_path)
    previous = _git("rev-parse", "HEAD", cwd=production)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "printf 'new_code = True' > injected.py"
    result = _run(env)
    assert result.returncode == 78
    assert _git("rev-parse", "HEAD", cwd=production) == previous
    assert (Path(_pending(home)["candidate"]) / "injected.py").exists()


def test_artifact_symlink_never_moves_or_reads_external_source(tmp_path):
    _, production, home, _ = _update_case(tmp_path)
    outside = home / "outside"
    outside.write_text("keep")
    (production / "output.log").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        evidence.archive_artifacts(production, home, _git("rev-parse", "HEAD", cwd=production))
    assert outside.read_text() == "keep"


def python_case(tmp_path):
    _, repo, home, _ = _update_case(tmp_path)
    (repo / "tests").mkdir()
    (repo / "scripts").mkdir(exist_ok=True)
    with (repo / ".gitignore").open("a") as stream:
        stream.write("__pycache__/\n")
    (repo / "runtime.py").write_text("version = 1\n")
    (repo / "tests/test_good.py").write_text("PASS\n")
    (repo / "tests/test_bad.py").write_text("FAIL\n")
    (repo / "scripts/run_tests_parallel.py").write_text('''
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def _discover_files(roots): return sorted((ROOT / "tests").glob("test_*.py"))
def _load_durations(root): return {}
def _run_one_file(path):
    with (ROOT / ".git/calls").open("a") as stream: stream.write(path.name + "\\n")
    rc = int("FAIL" in path.read_text())
    return path, rc, "output", {"failed" if rc else "passed": 1}, 0.1
def main():
    files = sys.argv[sys.argv.index("--files") + 1].split(":")
    return max(_run_one_file(ROOT / path)[1] for path in files)
''')
    commit(repo)
    return repo


def run_python(repo, state=None):
    arguments = [] if state is None else ['--state', str(state)]
    return subprocess.run([sys.executable, str(HELPER), "verify", str(repo), *arguments], capture_output=True, text=True)


def calls(repo):
    return (repo / ".git/calls").read_text().splitlines()


def test_test_only_repair_reuses_passing_files(tmp_path):
    repo = python_case(tmp_path)
    assert run_python(repo).returncode == 1
    (repo / "tests/test_bad.py").write_text("PASS\n")
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stderr
    assert "run=1 cached=1" in result.stdout
    assert calls(repo).count("test_good.py") == 1
    assert calls(repo).count("test_bad.py") == 2


@pytest.mark.parametrize("changed", ["runtime.py", "tests/conftest.py"])
def test_runtime_or_shared_fixture_change_invalidates_all_results(tmp_path, changed):
    repo = python_case(tmp_path)
    assert run_python(repo).returncode == 1
    (repo / "tests/test_bad.py").write_text("PASS\n")
    (repo / changed).write_text("version = 2\n")
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stderr
    assert "run=2 cached=0" in result.stdout
    assert calls(repo).count("test_good.py") == 2


def test_previous_collection_policy_cannot_satisfy_the_new_gate(tmp_path):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    commit(repo)
    assert run_python(repo).returncode == 0
    data = evidence.read_evidence(repo)
    data['signature'] = evidence.signature(data['files'], [
        'isolated-python-v2-private-tmp', sys.version,
        str(Path(sys.executable).resolve()), '',
    ])
    evidence.write_json(evidence.evidence_path(repo), data)
    evidence.evidence_path(repo).with_suffix('.jsonl').unlink()
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run=2 cached=0' in result.stdout
    assert calls(repo).count('test_good.py') == 2


def test_failed_files_run_before_full_gate_and_fail_fast(tmp_path):
    repo = python_case(tmp_path)
    assert run_python(repo).returncode == 1
    (repo / "runtime.py").write_text("version = 2\n")
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 1
    assert calls(repo).count("test_good.py") == 1
    assert calls(repo).count("test_bad.py") == 2


@pytest.mark.parametrize('fixed', [False, True])
def test_new_candidate_uses_failure_order_but_never_previous_passes(tmp_path, fixed):
    state = tmp_path / 'shared-state'
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    old = python_case(first)
    assert run_python(old, state).returncode == 1
    assert json.loads((state / 'python-failures.json').read_text()) == ['tests/test_bad.py']
    new = python_case(second)
    if fixed:
        (new / 'tests/test_bad.py').write_text('PASS\n')
        commit(new)
    result = run_python(new, state)
    assert result.returncode == (0 if fixed else 1), result.stdout + result.stderr
    assert 'run=2 cached=0' in result.stdout
    assert calls(new) == (['test_bad.py', 'test_good.py'] if fixed else ['test_bad.py'])
    assert json.loads((state / 'python-failures.json').read_text()) == ([] if fixed else ['tests/test_bad.py'])


@pytest.mark.parametrize('content', ['invalid json', '{}', '[null]', '["../../outside.py"]'])
def test_invalid_failure_order_cannot_change_test_coverage(tmp_path, content):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    commit(repo)
    state = tmp_path / 'shared-state'
    state.mkdir()
    (state / 'python-failures.json').write_text(content)
    result = run_python(repo, state)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(calls(repo)) == ['test_bad.py', 'test_good.py']


@pytest.mark.parametrize('fixed', [False, True])
def test_unwritable_failure_hints_do_not_change_verification_result(tmp_path, fixed):
    repo = python_case(tmp_path)
    if fixed:
        (repo / 'tests/test_bad.py').write_text('PASS\n')
        commit(repo)
    state = tmp_path / 'shared-state'
    (state / 'python-failures.json').mkdir(parents=True)
    result = run_python(repo, state)
    assert result.returncode == (0 if fixed else 1), result.stdout + result.stderr
    assert sorted(calls(repo)) == ['test_bad.py', 'test_good.py']
    assert 'PYTHON_PRIORITY_UNAVAILABLE' in result.stdout


@pytest.mark.parametrize('all_empty', [False, True])
def test_prior_failure_now_empty_still_checks_the_remaining_files(tmp_path, all_empty):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('EMPTY\n')
    (repo / 'tests/test_good.py').write_text('EMPTY\n' if all_empty else 'PASS\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}',
        '({} if "EMPTY" in path.read_text() else {"passed": 1})'
    ).replace(
        'return max(_run_one_file(ROOT / path)[1] for path in files)',
        'results = [_run_one_file(ROOT / path) for path in files]\n    return max(r[1] for r in results) if any(r[3] for r in results) else 1'
    ))
    commit(repo)
    state = tmp_path / 'shared-state'
    state.mkdir()
    (state / 'python-failures.json').write_text('["tests/test_bad.py"]')
    result = run_python(repo, state)
    assert result.returncode == int(all_empty), result.stdout + result.stderr
    assert calls(repo) == ['test_bad.py', 'test_good.py']
    assert (repo / '.git/hermes-collection-control-required').exists() == all_empty


def test_shared_test_module_reference_falls_back_to_full_gate(tmp_path):
    repo = python_case(tmp_path)
    (repo / "tests/test_good.py").write_text("# imports helpers from test_bad\n")
    commit(repo)
    assert run_python(repo).returncode == 1
    (repo / "tests/test_bad.py").write_text("PASS\n")
    commit(repo)
    assert run_python(repo).returncode == 0
    assert calls(repo).count("test_good.py") == 2


def test_partial_or_wrong_coverage_legacy_log_is_rejected(tmp_path):
    repo = python_case(tmp_path)
    log = tmp_path / "old.log"
    log.write_text("[100%] ✓ tests/test_good.py (1)\n")
    with pytest.raises(ValueError, match="incomplete"):
        evidence.seed_log(repo, log)
    log.write_text("[100%] ✓ tests/test_good.py (1)\n=== Summary: 1 files, 1 tests passed (100% complete)\n")
    with pytest.raises(ValueError, match="coverage"):
        evidence.seed_log(repo, log)


def test_terminal_target_does_not_hold_new_upstream_forever(tmp_path):
    upstream, production, home, env = _update_case(tmp_path)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/false"
    env["HERMES_UPDATE_REPAIR_COMMAND"] = "/bin/true"
    assert _run(env).returncode == 78
    old = _pending(home)
    assert _run(env).returncode == 78  # Never spend another budget on the same target.
    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "new upstream fix", cwd=upstream)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/true"
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REJECTED_TARGET_ARCHIVED" in result.stdout
    assert (production / "version.txt").read_text() == "three\n"
    archived = list((home / "state/rejected").glob("*/repair-pending"))
    assert len(archived) == 1 and f"target={old['target']}" in archived[0].read_text()
    assert Path(old["candidate"]).exists()


def test_transaction_retry_limit_does_not_reset_between_service_starts(tmp_path):
    _, _, home, env = _update_case(tmp_path)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "exit 69"
    for _ in range(4):
        assert _run(env).returncode == 69
    assert _run(env).returncode == 78
    assert (Path(_pending(home)["candidate"]) / ".git/hermes-run-count").read_text().strip() == "4"


def test_legacy_artifact_block_recovers_without_changing_target(tmp_path):
    _, production, home, env = _update_case(tmp_path)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "exit 69"
    assert _run(env).returncode == 69
    marker = _pending(home)
    candidate = Path(marker["candidate"])
    (candidate / "MagicMock").mkdir()
    (candidate / "MagicMock/123").write_bytes(b"SQLite format 3\x00test")
    (candidate / ".git/hermes-repair-blocked").write_text(
        f"BLOCKED verification changed candidate source or HEAD; target={marker['target']} candidate={candidate}\n")
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/true"
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECOVERED_TEST_ARTIFACTS" in result.stdout
    assert f"target={marker['target']}" in result.stdout
    assert (production / "version.txt").read_text() == "two\n"


def test_explicit_target_is_not_superseded_after_a_terminal_failure(tmp_path):
    upstream, _, home, env = _update_case(tmp_path)
    env["HERMES_UPDATE_VERIFY_COMMAND"] = "/bin/false"
    env["HERMES_UPDATE_REPAIR_COMMAND"] = "/bin/true"
    assert _run(env).returncode == 78
    target = _pending(home)["target"]
    env["HERMES_UPDATE_TARGET_SHA"] = target
    (upstream / "version.txt").write_text("three\n")
    _git("commit", "-am", "later upstream", cwd=upstream)
    assert _run(env).returncode == 78
    assert _pending(home)["target"] == target
    assert not (home / "state/rejected").exists()


@pytest.mark.parametrize('sandbox_failure', [True, False])
def test_sandbox_block_recovery_requires_exact_execution_evidence(tmp_path, sandbox_failure):
    import json
    _, _, home, env = _update_case(tmp_path)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = '/bin/false'
    env['HERMES_UPDATE_REPAIR_COMMAND'] = '/bin/true'
    assert _run(env).returncode == 78
    marker = _pending(home)
    candidate = Path(marker['candidate'])
    prefix = Path(marker['failure_log']).name.split('.verify-')[0]
    error = 'bwrap: loopback: Failed to create NETLINK_ROUTE socket: Address family not supported by protocol'
    (home / 'state' / (prefix + '.test-repair-1.jsonl')).write_text(json.dumps({
        'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 1,
        'aggregated_output': error if sandbox_failure else 'unsupported source fix'}}) + '\n')
    bin_dir = home / 'bin'
    bin_dir.mkdir()
    bwrap = bin_dir / 'bwrap'
    bwrap.write_text('#!/bin/sh\nexit 0\n')
    bwrap.chmod(0o755)
    env['PATH'] = str(bin_dir) + ':' + env['PATH']
    env['HERMES_UPDATE_REPAIR_COMMAND'] = 'test "$(cat .git/hermes-repair-count)" = 2 && touch repaired && git add repaired && git commit -m repaired'
    env['HERMES_UPDATE_VERIFY_COMMAND'] = '/bin/true'
    result = _run(env)
    assert result.returncode == (0 if sandbox_failure else 78), result.stdout + result.stderr
    assert ('RECOVERED_SANDBOX_START' in result.stdout) == sandbox_failure


def test_sandbox_preflight_failure_does_not_spend_a_repair_attempt(tmp_path):
    _, _, home, env = _update_case(tmp_path)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = '/bin/false'
    env.pop('HERMES_UPDATE_REPAIR_COMMAND')
    bin_dir = home / 'bin'
    bin_dir.mkdir()
    bwrap = bin_dir / 'bwrap'
    bwrap.write_text('#!/bin/sh\nexit 1\n')
    bwrap.chmod(0o755)
    env['PATH'] = str(bin_dir) + ':' + env['PATH']
    result = _run(env)
    assert result.returncode == 69, result.stdout + result.stderr
    candidate = Path(_pending(home)['candidate'])
    assert not (candidate / '.git/hermes-repair-count').exists()
    assert not (candidate / '.git/hermes-repair-blocked').exists()


def test_interrupted_runner_reuses_only_completed_journal_entries(tmp_path):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '    return max(_run_one_file(ROOT / path)[1] for path in files)',
        '''    results = []
    for path in files:
        results.append(_run_one_file(ROOT / path)[1])
        marker = ROOT / '.git/crash_once'
        if marker.exists():
            marker.unlink()
            __import__('os')._exit(9)
    return max(results)'''))
    commit(repo)
    (repo / '.git/crash_once').touch()
    assert run_python(repo).returncode == 9
    assert calls(repo) == ['test_bad.py']
    with (repo / '.git/hermes-python-results.jsonl').open('a') as stream:
        stream.write('{"truncated":')
    result = run_python(repo)
    assert result.returncode == 0, result.stderr
    assert 'run=1 cached=1' in result.stdout
    assert calls(repo) == ['test_bad.py', 'test_good.py']
    assert 'PYTHON_COLLECTION_CONTROL' not in result.stdout


@pytest.mark.parametrize('tail', ['truncated', 'no-newline'])
def test_second_interruption_preserves_results_after_a_truncated_journal(tmp_path, tail):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    (repo / 'tests/test_last.py').write_text('PASS\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '    return max(_run_one_file(ROOT / path)[1] for path in files)',
        '''    results = []
    for path in files:
        results.append(_run_one_file(ROOT / path)[1])
        if (ROOT / '.git/crash').exists():
            __import__('os')._exit(9)
    return max(results)'''))
    commit(repo)
    crash = repo / '.git/crash'
    crash.touch()
    assert run_python(repo).returncode == 9
    journal = repo / '.git/hermes-python-results.jsonl'
    if tail == 'truncated':
        with journal.open('a') as stream:
            stream.write('{"truncated":')
    else:
        journal.write_text(journal.read_text().rstrip('\n'))
    assert run_python(repo).returncode == 9
    assert set(evidence.read_evidence(repo)['results']) == {
        'tests/test_bad.py', 'tests/test_good.py',
    }
    crash.unlink()
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run=1 cached=2' in result.stdout
    assert calls(repo) == ['test_bad.py', 'test_good.py', 'test_last.py']


def test_journal_keeps_only_results_since_the_last_checkpoint(tmp_path):
    repo = python_case(tmp_path)
    assert run_python(repo).returncode == 1
    assert run_python(repo).returncode == 1
    journal = repo / '.git/hermes-python-results.jsonl'
    entries = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [entry['path'] for entry in entries] == ['tests/test_bad.py']
    assert set(evidence.read_evidence(repo)['results']) == {
        'tests/test_bad.py', 'tests/test_good.py',
    }


@pytest.mark.parametrize('boundary', ['before-truncate', 'after-truncate', 'after-invalidation'])
def test_interrupted_checkpoint_cannot_restore_invalidated_helper_results(tmp_path, monkeypatch, boundary):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    (repo / 'tests/test_good.py').write_text('# imports helpers from test_bad\n')
    commit(repo)
    assert run_python(repo).returncode == 0
    (repo / 'tests/test_bad.py').write_text('PASS\n# changed helper\n')
    commit(repo)
    current_files = evidence.source_map(repo)
    original_write = evidence.write_json
    original_unlink = Path.unlink
    journal = repo / '.git/hermes-python-results.jsonl'

    def interrupted_write(path, data):
        original_write(path, data)
        if boundary == 'after-invalidation' and data.get('files') == current_files:
            raise RuntimeError('simulated interruption')

    def interrupted_unlink(path, *args, **kwargs):
        if path == journal and boundary == 'before-truncate':
            raise RuntimeError('simulated interruption')
        original_unlink(path, *args, **kwargs)
        if path == journal and boundary == 'after-truncate':
            raise RuntimeError('simulated interruption')

    with monkeypatch.context() as patch:
        patch.setattr(evidence, 'write_json', interrupted_write)
        patch.setattr(Path, 'unlink', interrupted_unlink)
        with pytest.raises(RuntimeError, match='simulated interruption'):
            evidence.verify(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run=2 cached=0' in result.stdout
    assert calls(repo).count('test_good.py') == 2
    assert calls(repo).count('test_bad.py') == 2


def test_verification_stages_cannot_share_tmp_git_boundaries():
    import tempfile
    # The production candidate is outside /tmp; use /var/tmp for this fixture.
    with tempfile.TemporaryDirectory(prefix='hermes-namespace-test-', dir='/var/tmp') as directory:
        marker = Path(directory) / 'outside-tmp-preserved'
        first = subprocess.run([sys.executable, str(HELPER), 'isolate', '/bin/sh', '-c',
                                'mkdir /tmp/.git && touch "$1" && cat /dev/null >/dev/null', 'check', str(marker)],
                               capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        assert marker.exists()
        second = subprocess.run([sys.executable, str(HELPER), 'isolate', '/bin/sh', '-c',
                                 'test ! -e /tmp/.git && test -f "$1"', 'check', str(marker)],
                                capture_output=True, text=True)
        assert second.returncode == 0, second.stderr


def test_device_startup_block_returns_to_verification_without_resetting_counts(tmp_path):
    _, _, home, env = _update_case(tmp_path)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = '/bin/false'
    env['HERMES_UPDATE_REPAIR_COMMAND'] = '/bin/true'
    assert _run(env).returncode == 78
    marker = _pending(home)
    Path(marker['failure_log']).write_text("fatal: could not open '/dev/null' for reading and writing: Permission denied\n")
    bin_dir = home / 'bin'
    bin_dir.mkdir()
    bwrap = bin_dir / 'bwrap'
    bwrap.write_text('#!/bin/sh\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n')
    bwrap.chmod(0o755)
    env['PATH'] = str(bin_dir) + ':' + env['PATH']
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'test "$(cat .git/hermes-repair-count)" = 1'
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'RECOVERED_VERIFIER_DEVICE' in result.stdout


@pytest.mark.parametrize('rc', [0, 1])
def test_empty_collection_preserves_upstream_runner_exit_contract(tmp_path, rc):
    repo = python_case(tmp_path)
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace('rc = int("FAIL" in path.read_text())', f'rc = {rc} if path.name == "test_bad.py" else 0').replace('{"failed" if rc else "passed": 1}', '({} if path.name == "test_bad.py" else {"passed": 1})'))
    commit(repo)
    result = run_python(repo)
    assert result.returncode == rc, result.stdout + result.stderr
    data = evidence.read_evidence(repo)
    assert data['results']['tests/test_bad.py']['rc'] == rc
    if rc == 0:
        assert data['results']['tests/test_bad.py']['classification'] == 'no_tests_collected'


def test_empty_collection_migration_requires_complete_clean_evidence(tmp_path):
    repo = python_case(tmp_path)
    assert run_python(repo).returncode == 1
    data = evidence.read_evidence(repo)
    data['results'] = {name: {'rc': 1, 'summary': {}} for name in data['results']}
    evidence.write_json(evidence.evidence_path(repo), data)
    evidence.evidence_path(repo).with_suffix('.jsonl').unlink()
    log = tmp_path / 'completed.log'
    log.write_text('=== Summary: 2 files, 0 tests passed, 0 failed (100% complete) in 1s ===\n')
    evidence.validate_empty_recheck(repo, log)
    assert all(r['rc'] == 1 for r in evidence.read_evidence(repo)['results'].values())
    data['results']['tests/test_bad.py']['summary'] = {'failed': 1}
    evidence.write_json(evidence.evidence_path(repo), data)
    with pytest.raises(ValueError):
        evidence.validate_empty_recheck(repo, log)


def test_controller_revision_gets_bounded_retries_without_resetting_total(tmp_path):
    import shutil
    _, _, home, env = _update_case(tmp_path)
    controller = home / 'controller'
    controller.mkdir()
    script = controller / WEEKLY_UPDATER.name
    helper = controller / HELPER.name
    shutil.copy2(WEEKLY_UPDATER, script)
    for name in ('hermes-update-config', 'hermes-update-deployment'):
        shutil.copy2(WEEKLY_UPDATER.with_name(name), controller / name)
    shutil.copy2(HELPER, helper)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'exit 69'
    def start():
        return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=30)
    for _ in range(4):
        assert start().returncode == 69
    assert start().returncode == 78
    candidate = Path(_pending(home)['candidate'])
    with helper.open('a') as stream:
        stream.write('\n# Simulated installed controller fix.\n')
    result = start()
    assert result.returncode == 69, result.stdout + result.stderr
    assert 'RECOVERED_CONTROLLER_REVISION' in result.stdout
    assert (candidate / '.git/hermes-run-count').read_text().strip() == '5'
    assert sorted(p.read_text().strip() for p in (candidate / '.git').glob('hermes-controller-runs.*')) == ['1', '4']
    assert not (candidate / '.git/hermes-repair-count').exists()


def test_empty_only_cached_subset_includes_a_real_collection_control(tmp_path):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    (repo / 'tests/test_good.py').write_text('EMPTY\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace('{"failed" if rc else "passed": 1}',
                                                '({} if "EMPTY" in path.read_text() else {"passed": 1})')
                      .replace('return max(_run_one_file(ROOT / path)[1] for path in files)',
                               'results = [_run_one_file(ROOT / path) for path in files]\n    return max(r[1] for r in results) if any(r[3] for r in results) else 1'))
    commit(repo)
    assert run_python(repo).returncode == 0
    (repo / 'tests/test_good.py').write_text('EMPTY\n# changed standalone smoke module\n')
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PYTHON_COLLECTION_CONTROL file=tests/test_bad.py' in result.stdout
    assert calls(repo).count('test_bad.py') == 2
    assert calls(repo).count('test_good.py') == 2


@pytest.mark.parametrize('control', ['PASS', 'EMPTY', 'FAIL'])
@pytest.mark.parametrize('shared_change', [False, True])
def test_repaired_failure_with_no_collection_uses_current_control_result(tmp_path, control, shared_change):
    repo = python_case(tmp_path)
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}',
        '({} if "EMPTY" in path.read_text() else {"failed" if rc else "passed": 1})'
    ).replace(
        'return max(_run_one_file(ROOT / path)[1] for path in files)',
        'results = [_run_one_file(ROOT / path) for path in files]\n    return max(r[1] for r in results) if any(r[3] for r in results) else 1'
    ))
    commit(repo)
    assert run_python(repo).returncode == 1
    (repo / 'tests/test_bad.py').write_text('EMPTY\n')
    (repo / 'tests/test_good.py').write_text(control + '\n')
    if shared_change:
        (repo / 'runtime.py').write_text('version = 2\n')
    commit(repo)
    result = run_python(repo)
    assert result.returncode == (0 if control == 'PASS' else 1), result.stdout + result.stderr
    assert 'PYTHON_COLLECTION_CONTROL file=tests/test_good.py' in result.stdout
    assert calls(repo).count('test_bad.py') == 2
    assert calls(repo).count('test_good.py') == 2


def test_interruption_before_collection_guard_requires_a_live_control(tmp_path):
    repo = python_case(tmp_path)
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}',
        '({} if "EMPTY" in path.read_text() else {"failed" if rc else "passed": 1})'
    ).replace(
        '    return max(_run_one_file(ROOT / path)[1] for path in files)',
        '''    results = [_run_one_file(ROOT / path) for path in files]
    marker = ROOT / '.git/crash_before_guard'
    if marker.exists():
        marker.unlink()
        __import__('os')._exit(9)
    return max(r[1] for r in results) if any(r[3] for r in results) else 1'''
    ))
    commit(repo)
    assert run_python(repo).returncode == 1
    (repo / 'tests/test_bad.py').write_text('EMPTY\n')
    commit(repo)
    (repo / '.git/crash_before_guard').touch()
    assert run_python(repo).returncode == 9
    assert (repo / '.git/hermes-collection-control-required').exists()
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run=1 cached=1' in result.stdout
    assert calls(repo) == ['test_bad.py', 'test_good.py', 'test_bad.py', 'test_good.py']
    assert not (repo / '.git/hermes-collection-control-required').exists()


def test_later_empty_batch_reuses_a_completed_live_control(tmp_path):
    repo = python_case(tmp_path)
    (repo / 'tests/test_last.py').write_text('EMPTY\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}',
        '({} if "EMPTY" in path.read_text() else {"failed" if rc else "passed": 1})'
    ).replace(
        'return max(_run_one_file(ROOT / path)[1] for path in files)',
        'results = [_run_one_file(ROOT / path) for path in files]\n    return max(r[1] for r in results) if any(r[3] for r in results) else 1'
    ))
    commit(repo)
    assert run_python(repo).returncode == 1
    (repo / 'tests/test_bad.py').write_text('EMPTY\n')
    (repo / 'runtime.py').write_text('version = 2\n')
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(repo) == ['test_bad.py', 'test_good.py', 'test_last.py'] * 2
    assert not (repo / '.git/hermes-collection-control-required').exists()


@pytest.mark.parametrize('invalidate_shared_source', [False, True], ids=['cached-control', 'pending-control'])
def test_empty_historical_control_does_not_hide_a_remaining_real_test(tmp_path, invalidate_shared_source):
    repo = python_case(tmp_path)
    (repo / 'tests/test_last.py').write_text('PASS\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}',
        '({} if "EMPTY" in path.read_text() else {"failed" if rc else "passed": 1})'
    ).replace(
        'return max(_run_one_file(ROOT / path)[1] for path in files)',
        'results = [_run_one_file(ROOT / path) for path in files]\n    return max(r[1] for r in results) if any(r[3] for r in results) else 1'
    ))
    commit(repo)
    assert run_python(repo).returncode == 1
    (repo / 'tests/test_bad.py').write_text('EMPTY\n')
    (repo / 'tests/test_good.py').write_text('EMPTY\n')
    if invalidate_shared_source:
        (repo / 'runtime.py').write_text('version = 2\n')
    commit(repo)
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(repo) == ['test_bad.py', 'test_good.py', 'test_last.py'] * 2
    assert not (repo / '.git/hermes-collection-control-required').exists()


@pytest.mark.parametrize('outcome', ['passed', 'skipped', 'xfailed'])
def test_interrupted_collected_results_can_supply_a_live_control(tmp_path, outcome):
    repo = python_case(tmp_path)
    (repo / 'tests/test_bad.py').write_text('PASS\n')
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace(
        '{"failed" if rc else "passed": 1}', repr({outcome: 1})
    ).replace(
        '    return max(_run_one_file(ROOT / path)[1] for path in files)',
        '''    results = [_run_one_file(ROOT / path) for path in files]
    marker = ROOT / '.git/crash_once'
    if marker.exists():
        marker.unlink()
        __import__('os')._exit(9)
    return max(r[1] for r in results)'''
    ))
    commit(repo)
    (repo / '.git/crash_once').touch()
    assert run_python(repo).returncode == 9
    result = run_python(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run=1 cached=1' in result.stdout
    assert len(calls(repo)) == 3
    assert not (repo / '.git/hermes-collection-control-required').exists()


def test_zero_collected_full_matrix_cannot_become_a_cached_pass(tmp_path):
    repo = python_case(tmp_path)
    runner = repo / 'scripts/run_tests_parallel.py'
    runner.write_text(runner.read_text().replace('rc = int("FAIL" in path.read_text())', 'rc = 0')
                      .replace('{"failed" if rc else "passed": 1}', '{}'))
    commit(repo)
    assert run_python(repo).returncode == 1
    assert run_python(repo).returncode == 1


def test_deployment_retries_are_bounded_and_keep_pending_recovery(tmp_path):
    _, production, home, env = _update_case(tmp_path)
    env['HERMES_UPDATE_DEPLOY_COMMAND'] = 'exit 69'
    for _ in range(4):
        assert _run(env).returncode == 69
    head = _git('rev-parse', 'HEAD', cwd=production)
    result = _run(env)
    assert result.returncode == 78
    assert 'deployment retry budget exhausted' in result.stderr
    assert (home / 'state/promotion-pending').exists()
    assert _git('rev-parse', 'HEAD', cwd=production) == head
    assert [p.read_text().strip() for p in (home / 'state').glob('deployment-attempts.*')] == ['4']


def test_runtime_package_snapshot_survives_failed_extra_restore(tmp_path, monkeypatch):
    import types
    from test_hermes_updater_unattended import _python_tool
    _, repo, home, _ = _update_case(tmp_path)
    (repo / 'hermes_cli').mkdir()
    (repo / 'hermes_cli/sqlite_runtime.py').write_text('# runtime probe fixture\n')
    _git('add', 'hermes_cli', cwd=repo)
    _git('commit', '-m', 'runtime fixture', cwd=repo)
    state = home / 'state'
    uv = home / 'bin/uv'
    events = home / 'uv-events'
    failed = home / 'install-failed-once'
    _python_tool(uv, f'''
import sys
from pathlib import Path
args = sys.argv[1:]
with Path({str(events)!r}).open('a') as stream: stream.write(args[1] + '\\n')
if args[1] == 'freeze': print('host-extra==1.2.3')
if args[1] == 'install': assert '--exclude-newer' in args
if args[1] == 'install' and not Path({str(failed)!r}).exists():
    Path({str(failed)!r}).touch()
    sys.exit(1)
''')
    monkeypatch.setattr(evidence.shutil, 'which', lambda name: str(uv))
    monkeypatch.setattr(evidence, 'runtime_info', lambda root: types.SimpleNamespace(
        sqlite_version_string='3.51.3', wal_reset_vulnerable=False))
    monkeypatch.setitem(sys.modules, 'hermes_cli.managed_uv', types.SimpleNamespace(
        ensure_uv=lambda: str(uv), resolve_uv=lambda: str(uv),
        repair_vulnerable_runtime=lambda *a, **kw: types.SimpleNamespace(status='safe')))
    with pytest.raises(subprocess.CalledProcessError):
        evidence.runtime_repair(repo, state)
    assert (state / 'runtime-repair/packages.txt').read_text() == 'host-extra==1.2.3\n'
    evidence.runtime_repair(repo, state)
    assert events.read_text().splitlines() == ['freeze', 'install', 'install', 'check']
    assert not (state / 'runtime-repair').exists()
    assert len(list(state.glob('runtime-repair-completed.*/packages.txt'))) == 1


def test_no_update_repairs_runtime_with_pending_state_and_then_becomes_cheap(tmp_path):
    import shutil
    from test_hermes_updater_unattended import _python_tool
    _, repo, home, env = _update_case(tmp_path)
    env.pop('HERMES_UPDATE_DEPLOY_COMMAND')
    env['HERMES_UPDATE_PYTHON'] = 'off'
    (home / '.hermes').mkdir()
    env['HERMES_UPDATE_TARGET_SHA'] = _git('rev-parse', 'HEAD', cwd=repo)
    controller = home / 'controller'
    controller.mkdir()
    script = controller / WEEKLY_UPDATER.name
    shutil.copy2(WEEKLY_UPDATER, script)
    for name in ('hermes-update-config', 'hermes-update-deployment'):
        shutil.copy2(WEEKLY_UPDATER.with_name(name), controller / name)
    events, safe = home / 'events', home / 'runtime-safe'
    helper = controller / HELPER.name
    _python_tool(helper, f'''
import os, sys
from pathlib import Path
if sys.argv[1] == 'runtime-check': sys.exit(0 if Path({str(safe)!r}).exists() else 69)
if sys.argv[1] == 'runtime-repair':
    assert Path({str(home / 'state/promotion-pending')!r}).exists()
    with Path({str(events)!r}).open('a') as stream: stream.write('runtime-repair\\n')
    Path({str(safe)!r}).touch()
    sys.exit(0)
os.execv(sys.executable, [sys.executable, {str(HELPER)!r}, *sys.argv[1:]])
''')
    _python_tool(home / 'bin/systemctl', f'''
import sys
from pathlib import Path
with Path({str(events)!r}).open('a') as stream: stream.write(' '.join(sys.argv[1:]) + '\\n')
if 'list-units' in sys.argv: print('hermes-gateway.service loaded active running')
if '--property=Environment' in sys.argv: print('Environment=VIRTUAL_ENV={repo}/venv HERMES_HOME={home}/.hermes')
''')
    _python_tool(repo / 'venv/bin/python', 'pass\n')
    env['PATH'] = str(home / 'bin') + ':' + env['PATH']
    def start():
        return subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=30)
    first = start()
    assert first.returncode == 0, first.stdout + first.stderr
    assert not (home / 'state/promotion-pending').exists()
    assert _git('rev-parse', 'HEAD', cwd=repo) == env['HERMES_UPDATE_TARGET_SHA']
    calls = events.read_text()
    assert 'stop hermes-gateway.service' in calls
    assert 'stop hermes-gateway.service hermes-dashboard.service' not in calls
    assert 'runtime-repair' in calls
    assert 'restart hermes-gateway.service' in calls
    assert 'restart hermes-gateway.service hermes-dashboard.service' not in calls
    second = start()
    assert second.returncode == 0, second.stdout + second.stderr
    new_calls = events.read_text()[len(calls):]
    assert "runtime-repair" not in new_calls
    assert "stop " not in new_calls
    assert "restart " not in new_calls
