"""Exercise user settings through target selection, deployment, and recovery."""
import subprocess

from test_hermes_reliability import _git, WEEKLY_UPDATER
from test_hermes_updater_unattended import _update_case, _run, _python_tool


def minimal_case(tmp_path):
    upstream, repo, home, env = _update_case(tmp_path)
    env.pop('HERMES_UPDATE_DEPLOY_COMMAND')
    env.pop('HERMES_UPDATE_HEALTH_COMMAND')
    env.pop('HERMES_UPDATE_MAX_REPAIRS')
    _python_tool(home / 'bin/systemctl', 'import sys; sys.exit(1)\n')
    env['PATH'] = str(home / 'bin') + ':' + env['PATH']
    return upstream, repo, home, env


def test_blank_checkout_updates_without_hermes_configuration_or_services(tmp_path):
    upstream, repo, home, env = minimal_case(tmp_path)
    target = _git('rev-parse', 'HEAD', cwd=upstream)
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PROMOTED' in result.stdout
    assert _git('merge-base', '--is-ancestor', target, 'HEAD', cwd=repo) == ''
    assert not (repo / 'venv').exists()
    assert not (repo / 'node_modules').exists()
    assert not (home / '.hermes').exists()
    assert not list((home / 'state').glob('*-pending'))


def test_dependency_scope_refuses_target_before_creating_transaction(tmp_path):
    upstream, repo, home, env = minimal_case(tmp_path)
    previous = _git('rev-parse', 'HEAD', cwd=repo)
    _python_tool(repo / 'venv/bin/python', 'pass\n')
    config = home / '.config/awesome-hermes-updates/config'
    config.parent.mkdir(parents=True)
    config.write_text('HERMES_UPDATE_PYTHON=off\nHERMES_UPDATE_RUNTIME=off\n')
    (upstream / 'pyproject.toml').write_text('[project]\nname="changed-dependencies"\n')
    _git('commit', '-am', 'new dependencies', cwd=upstream)
    result = _run(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert 'dependencies changed while python=off' in result.stderr
    assert _git('rev-parse', 'HEAD', cwd=repo) == previous
    assert not list((home / 'state').glob('*-pending'))
    assert not (home / 'state/deployment-profiles').exists()


def test_repairs_require_explicit_opt_in(tmp_path):
    _, repo, home, env = minimal_case(tmp_path)
    previous = _git('rev-parse', 'HEAD', cwd=repo)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'false'
    env['HERMES_UPDATE_REPAIR_COMMAND'] = 'touch "$HOME/repair-was-run"'
    result = _run(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert 'limit=0' in result.stderr
    assert not (home / 'repair-was-run').exists()
    assert _git('rev-parse', 'HEAD', cwd=repo) == previous


def test_failed_noop_health_does_not_strand_scope_snapshot(tmp_path):
    _, repo, home, env = minimal_case(tmp_path)
    env['HERMES_UPDATE_TARGET_SHA'] = _git('rev-parse', 'HEAD', cwd=repo)
    env['HERMES_UPDATE_HEALTH_COMMAND'] = 'false'
    assert _run(env).returncode != 0
    assert not (home / 'state/deployment-profiles').exists()
    env['HERMES_UPDATE_RUNTIME'] = 'off'
    env['HERMES_UPDATE_HEALTH_COMMAND'] = 'true'
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plan_resolves_scope_without_fetch_or_pending_state(tmp_path):
    _, repo, home, env = minimal_case(tmp_path)
    _git('remote', 'set-url', 'origin', str(tmp_path / 'unreachable'), cwd=repo)
    result = subprocess.run([str(WEEKLY_UPDATER), '--plan'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'python=0 node=0 dashboard=0 gateway=0 migrate=0 runtime=0' in result.stdout
    assert not (home / 'state/deployment-profiles').exists()
    assert not list((home / 'state').glob('*-pending'))
