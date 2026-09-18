"""Opt into repairs after a zero-budget stop without replacing the transaction."""
from pathlib import Path
import subprocess

import pytest

from test_configuration import config, ROOT
from test_hermes_reliability import _git
from test_hermes_updater_unattended import _pending, _run, _update_case


def blocked_case(tmp_path):
    upstream, production, home, env = _update_case(tmp_path)
    env.pop('HERMES_UPDATE_MAX_REPAIRS')
    path = config['config_path'](home)
    config['write_config'](path, {
        'HERMES_UPDATE_REPO': str(production),
        'HERMES_UPDATE_STATE_DIR': str(home / 'state'),
    })
    env['HERMES_UPDATE_VERIFY_COMMAND'] = 'test -f repaired.txt'
    env['HERMES_UPDATE_REPAIR_COMMAND'] = (
        'printf "fixed\\n" > repaired.txt; git add repaired.txt; git commit -m repair'
    )
    result = _run(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert 'count=0 limit=0' in result.stderr
    return upstream, production, home, env


def configure(env, *arguments):
    return subprocess.run([str(ROOT / 'bin/hermes-updates'), 'configure',
                           *arguments, '--no-systemd'], env=env,
                          capture_output=True, text=True, timeout=10)


def test_opt_in_resumes_original_candidate_and_preserves_block_evidence(tmp_path):
    upstream, production, home, env = blocked_case(tmp_path)
    pending = _pending(home)
    candidate = Path(pending['candidate'])
    blocked = candidate / '.git/hermes-repair-blocked'
    original_block = blocked.read_bytes()
    marker = (home / 'state/repair-pending').read_bytes()
    # Even an old candidate and newer upstream must not redirect this opt-in.
    (candidate / '.git/hermes-transaction-started').write_text('1\n')
    (upstream / 'newer.txt').write_text('not this transaction\n')
    _git('add', 'newer.txt', cwd=upstream)
    _git('commit', '-m', 'newer upstream', cwd=upstream)

    result = configure(env, '--repair-agent', 'codex', '--max-repairs', '4')
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / 'state/repair-pending').read_bytes() == marker
    assert blocked.read_bytes() == original_block

    # Retain the candidate for inspection by failing deployment after the gate.
    env['HERMES_UPDATE_DEPLOY_COMMAND'] = 'false'
    result = _run(env)
    assert 'RECOVERED_REPAIR_OPT_IN' in result.stdout, result.stdout + result.stderr
    assert _pending(home)['target'] == pending['target']
    assert _pending(home)['candidate'] == str(candidate)
    assert not (production / 'newer.txt').exists()
    assert (production / 'repaired.txt').exists()
    assert (candidate / '.git/hermes-repair-count').read_text().strip() == '1'
    archives = list((candidate / '.git').glob('hermes-disabled-repair-block.recovered.*'))
    assert len(archives) == 1 and archives[0].read_bytes() == original_block
    assert not blocked.exists()

    env['HERMES_UPDATE_DEPLOY_COMMAND'] = 'true'
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list((home / 'state').glob('*-pending'))
    _git('merge-base', '--is-ancestor', pending['target'], 'HEAD', cwd=production)


@pytest.mark.parametrize('arguments', [
    ['--max-repairs', '4', '--gateway', 'off'],
    ['--max-repairs', '4', '--repo', '/different/repo'],
    ['--max-repairs', '4', '--ref', 'different-target'],
])
def test_opt_in_cannot_expand_pending_scope(tmp_path, arguments):
    _, _, home, env = blocked_case(tmp_path)
    path = config['config_path'](home)
    before = path.read_bytes()
    assert configure(env, *arguments).returncode == 78
    assert path.read_bytes() == before


def test_opt_in_still_requires_exclusive_update_lock(tmp_path):
    import fcntl

    _, _, home, env = blocked_case(tmp_path)
    with (home / 'state/update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = configure(env, '--max-repairs', '4')
    assert result.returncode == 78 and 'running' in result.stderr


@pytest.mark.parametrize('reason,count', [
    ('total repair budget exhausted or invalid: count=1 limit=1', '1'),
    ('repair made no committed progress', '0'),
    ('total repair budget exhausted or invalid: count=0 limit=0', 'invalid'),
])
def test_opt_in_does_not_clear_other_blocks_or_invalid_counters(tmp_path, reason, count):
    _, production, home, env = blocked_case(tmp_path)
    previous = _git('rev-parse', 'HEAD', cwd=production)
    pending = _pending(home)
    candidate = Path(pending['candidate'])
    blocked = candidate / '.git/hermes-repair-blocked'
    blocked.write_text(f'BLOCKED {reason}; target={pending["target"]} candidate={candidate}\n')
    (candidate / '.git/hermes-repair-count').write_text(count + '\n')
    before = blocked.read_bytes()
    env.update(HERMES_UPDATE_MAX_REPAIRS='4', HERMES_UPDATE_TARGET_SHA=pending['target'])
    result = _run(env)
    assert result.returncode == 78, result.stdout + result.stderr
    assert blocked.read_bytes() == before
    assert _git('rev-parse', 'HEAD', cwd=production) == previous
