"""One operator-requested recheck cannot replenish exhausted source repairs."""
from pathlib import Path
import subprocess

import pytest

from test_configuration import ROOT
from test_hermes_reliability import _git, WEEKLY_UPDATER
from test_hermes_updater_unattended import _pending, _run, _update_case


def exhausted_case(tmp_path):
    upstream, production, home, env = _update_case(tmp_path)
    env.update(HERMES_UPDATE_MAX_REPAIRS='1', HERMES_UPDATE_VERIFY_COMMAND='false',
               HERMES_UPDATE_REPAIR_COMMAND='touch fixed; git add fixed; git commit -m repair')
    result = _run(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert 'count=1 limit=1' in result.stderr
    return upstream, production, home, env


def reverify(env):
    return subprocess.run([str(WEEKLY_UPDATER), '--reverify'], env=env,
                          capture_output=True, text=True, timeout=30)


def test_verification_only_recovery_keeps_target_budget_and_prior_evidence(tmp_path):
    upstream, production, home, env = exhausted_case(tmp_path)
    pending = _pending(home)
    candidate = Path(pending['candidate'])
    block = (candidate / '.git/hermes-repair-blocked').read_bytes()
    (upstream / 'newer').touch()
    _git('add', 'newer', cwd=upstream)
    _git('commit', '-m', 'later upstream', cwd=upstream)
    (candidate / '.git/hermes-transaction-started').write_text('1\n')
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'true'
    env['HERMES_UPDATE_DEPLOY_COMMAND'] = 'false'  # retain the verified candidate
    result = reverify(env)
    assert 'REVERIFY_PENDING' in result.stdout, result.stdout + result.stderr
    assert (home / 'state/promotion-pending').exists()
    assert _pending(home)['target'] == pending['target']
    assert _pending(home)['candidate'] == str(candidate)
    assert (candidate / '.git/hermes-repair-count').read_text().strip() == '1'
    assert (candidate / '.git/hermes-reverify-checkpoint').read_text().strip() == pending['checkpoint']
    archives = list((candidate / '.git').glob('hermes-reverify-block.*'))
    assert len(archives) == 1 and archives[0].read_bytes() == block
    assert not (production / 'newer').exists()
    env['HERMES_UPDATE_DEPLOY_COMMAND'] = 'true'
    result = reverify(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list((home / 'state').glob('*-pending'))


def test_failed_reverification_cannot_repeat_or_run_more_repairs(tmp_path):
    _, production, home, env = exhausted_case(tmp_path)
    pending = _pending(home)
    previous = _git('rev-parse', 'HEAD', cwd=production)
    candidate = Path(pending['candidate'])
    env.update(HERMES_UPDATE_TARGET_SHA=pending['target'], HERMES_UPDATE_MAX_REPAIRS='4',
               HERMES_UPDATE_REPAIR_COMMAND='touch "$HOME/forbidden-repair"',
               HERMES_UPDATE_VERIFY_COMMAND='echo check >> "$HOME/verifications"; false')
    result = reverify(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert 'verification-only recovery failed' in result.stderr
    assert (home / 'verifications').read_text().splitlines() == ['check']
    assert not (home / 'forbidden-repair').exists()
    for command in (reverify, _run):
        assert command(env).returncode == 78
        assert (home / 'verifications').read_text().splitlines() == ['check']
    assert _git('rev-parse', 'HEAD', cwd=production) == previous
    assert (candidate / '.git/hermes-repair-count').read_text().strip() == '1'


@pytest.mark.parametrize('change', ['dirty', 'new-head', 'invalid-count', 'other-block'])
def test_reverification_refuses_changed_or_invalid_candidate(tmp_path, change):
    _, production, home, env = exhausted_case(tmp_path)
    pending = _pending(home)
    candidate = Path(pending['candidate'])
    if change in ('dirty', 'new-head'):
        (candidate / 'version.txt').write_text('unverified\n')
        if change == 'new-head':
            _git('commit', '-am', 'unverified change', cwd=candidate)
    elif change == 'invalid-count':
        (candidate / '.git/hermes-repair-count').write_text('invalid\n')
    else:
        (candidate / '.git/hermes-repair-blocked').write_text('BLOCKED no progress\n')
    before = (home / 'state/repair-pending').read_bytes()
    previous = _git('rev-parse', 'HEAD', cwd=production)
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'true'
    assert reverify(env).returncode == 78
    assert (home / 'state/repair-pending').read_bytes() == before
    assert not (candidate / '.git/hermes-reverify-checkpoint').exists()
    assert _git('rev-parse', 'HEAD', cwd=production) == previous


def test_reverification_requires_existing_transaction_and_cli_forwards_flag(tmp_path):
    _, production, home, env = _update_case(tmp_path)
    previous = _git('rev-parse', 'HEAD', cwd=production)
    result = subprocess.run([str(ROOT / 'bin/hermes-updates'), 'run', '--direct', '--reverify'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 78
    assert 'requires a pending transaction' in result.stderr
    assert _git('rev-parse', 'HEAD', cwd=production) == previous
    assert not list((home / 'state').glob('*-pending'))


def test_interrupted_environment_reuses_the_one_shot_without_more_repairs(tmp_path):
    _, _, home, env = exhausted_case(tmp_path)
    candidate = Path(_pending(home)['candidate'])
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'exit 69'
    assert reverify(env).returncode == 69
    receipt = (candidate / '.git/hermes-reverify-checkpoint').read_bytes()
    assert _pending(home)['phase'] == 'verify'
    env.update(HERMES_UPDATE_VERIFY_COMMAND='true', HERMES_UPDATE_DEPLOY_COMMAND='false')
    result = reverify(env)
    assert (home / 'state/promotion-pending').exists(), result.stdout + result.stderr
    assert (candidate / '.git/hermes-reverify-checkpoint').read_bytes() == receipt
    assert (candidate / '.git/hermes-repair-count').read_text().strip() == '1'


def test_reverification_cannot_create_a_candidate_for_selected_target(tmp_path):
    upstream, production, home, env = _update_case(tmp_path)
    _git('fetch', 'origin', cwd=production)
    state = home / 'state'
    state.mkdir()
    marker = state / 'repair-pending'
    marker.write_text(f'version=1\nproduction={_git("rev-parse", "HEAD", cwd=production)}\n'
                      f'target={_git("rev-parse", "HEAD", cwd=upstream)}\n'
                      'phase=selected\ncandidate=-\ncheckpoint=-\nfailure_log=-\n')
    before = marker.read_bytes()
    result = reverify(env)
    assert result.returncode == 78 and 'requires a saved candidate' in result.stderr
    assert marker.read_bytes() == before
    assert not list(state.glob('candidate.*'))
