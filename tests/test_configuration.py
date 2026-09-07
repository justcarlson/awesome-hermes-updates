"""Configuration is literal, optional, and cannot redirect pending recovery."""
import os
from pathlib import Path
import runpy
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
config = runpy.run_path(str(ROOT / 'bin/hermes-update-config'))


def test_minimal_defaults_follow_custom_hermes_home(tmp_path):
    settings = config['effective']({}, home=tmp_path, environ={'HERMES_HOME': str(tmp_path / 'custom')})
    assert settings['HERMES_UPDATE_REPO'] == str(tmp_path / 'custom/hermes-agent')
    assert settings['HERMES_UPDATE_HOME'] == str(tmp_path / 'custom')
    assert settings['HERMES_UPDATE_MAX_REPAIRS'] == '0'
    assert settings['HERMES_UPDATE_REPAIR_AGENT'] == 'codex'
    assert settings['HERMES_UPDATE_EXTRAS'] == ''
    assert settings['HERMES_UPDATE_SCHEDULE'] == 'on-demand'
    assert all(settings['HERMES_UPDATE_' + key] == 'auto' for key in config['COMPONENTS'])


@pytest.mark.parametrize('writer', ['configure', 'install'])
def test_stale_configuration_cannot_overwrite_a_completed_writer(tmp_path, monkeypatch, capsys, writer):
    from contextlib import contextmanager
    import sys

    monkeypatch.setenv('HOME', str(tmp_path))
    for key in list(os.environ):
        if key.startswith('HERMES_UPDATE_') or key == 'HERMES_HOME':
            monkeypatch.delenv(key)
    path = config['config_path'](tmp_path)
    config['write_config'](path, {'HERMES_UPDATE_GATEWAY': 'auto'})
    helper = dict(config)
    winner = {'HERMES_UPDATE_GATEWAY': 'off'}

    @contextmanager
    def competing_writer(settings, **kwargs):
        # The other writer completes after our read, but before we take the lock.
        config['write_config'](path, winner)
        with config['settings_lock'](settings, **kwargs):
            yield

    helper['settings_lock'] = competing_writer
    load = runpy.run_path
    if writer == 'configure':
        program = load(str(ROOT / 'bin/hermes-updates'))
        program['config'].update(helper)
        monkeypatch.setattr(sys, 'argv', ['hermes-updates', 'configure', '--python', 'off', '--no-systemd'])
        assert program['main']() == 78
        assert 'configuration changed' in capsys.readouterr().err
    else:
        program = load(str(ROOT / 'install'))
        monkeypatch.setattr(runpy, 'run_path', lambda source: helper)
        monkeypatch.setattr(sys, 'argv', ['install', '--stage', str(tmp_path), '--repo', str(tmp_path / 'new')])
        with pytest.raises(ValueError, match='configuration changed'):
            program['main']()
        assert not (tmp_path / '.local/share/awesome-hermes-updates/current').exists()
    assert config['read_config'](path) == winner


def test_config_roundtrip_is_literal_and_environment_wins(tmp_path):
    path = tmp_path / 'config'
    marker = tmp_path / 'must-not-exist'
    values = {'HERMES_UPDATE_REPO': str(tmp_path / 'a path'),
              'HERMES_UPDATE_HEALTH_COMMAND': f'$(touch {marker}) "quoted" \\ literal',
              'HERMES_UPDATE_PYTHON': 'off'}
    config['write_config'](path, values)
    assert config['read_config'](path) == values
    settings = config['effective'](values, home=tmp_path, environ={'HERMES_UPDATE_PYTHON': 'on'})
    assert settings['HERMES_UPDATE_PYTHON'] == 'on'
    assert not marker.exists()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('key,value', [
    ('HERMES_UPDATE_REPO', 'relative'), ('HERMES_UPDATE_PYTHON', 'yes'),
    ('HERMES_UPDATE_MAX_REPAIRS', '21'), ('HERMES_UPDATE_MAX_REPAIRS', '-1'),
    ('HERMES_UPDATE_REPAIR_AGENT', 'other'),
    ('HERMES_UPDATE_TEST_WORKERS', '0'), ('HERMES_UPDATE_PROFILES', '../bad'),
    ('HERMES_UPDATE_PROFILES', ''), ('HERMES_UPDATE_PROFILES', 'x,x'),
    ('HERMES_UPDATE_EXTRAS', 'dev,--bad'), ('HERMES_UPDATE_HOME', '/tmp/x\nInjected=yes'),
])
def test_invalid_config_rejected(key, value, tmp_path):
    with pytest.raises(ValueError):
        config['effective']({key: value}, home=tmp_path, environ={})


def cli(home, *arguments, extra_env=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('HERMES_UPDATE_') and key != 'HERMES_HOME'}
    environment.update(HOME=str(home))
    environment.update(extra_env or {})
    return subprocess.run([str(ROOT / 'bin/hermes-updates'), *arguments], env=environment, text=True, capture_output=True)


def test_configuration_requires_no_hermes_setup_and_failed_change_is_atomic(tmp_path):
    result = cli(tmp_path, 'configure', '--repo', str(tmp_path / 'not installed'), '--python', 'off', '--no-systemd')
    assert result.returncode == 0, result.stderr
    path = config['config_path'](tmp_path)
    before = path.read_bytes()
    result = cli(tmp_path, 'configure', '--max-repairs', '30', '--no-systemd')
    assert result.returncode == 78
    assert path.read_bytes() == before
    result = cli(tmp_path, 'plan')
    assert 'python: off' in result.stdout
    assert 'whole Git commit' in result.stdout


def test_pending_recovery_blocks_scope_change(tmp_path):
    state = tmp_path / '.local/state/hermes-weekly-update'
    state.mkdir(parents=True)
    (state / 'promotion-pending').write_text('pending evidence')
    result = cli(tmp_path, 'configure', '--gateway', 'off', '--no-systemd')
    assert result.returncode == 78
    assert 'pending' in result.stderr
    assert not config['config_path'](tmp_path).exists()


def test_repair_agent_selection_is_saved_without_enabling_repairs(tmp_path):
    result = cli(tmp_path, 'configure', '--repair-agent', 'claude', '--no-systemd')
    assert result.returncode == 0, result.stderr
    saved = config['read_config'](config['config_path'](tmp_path))
    settings = config['effective'](saved, home=tmp_path, environ={})
    assert settings['HERMES_UPDATE_REPAIR_AGENT'] == 'claude'
    assert settings['HERMES_UPDATE_MAX_REPAIRS'] == '0'
    assert cli(tmp_path, 'configure', '--repair-agent', 'claude', '--no-systemd').returncode == 0
    assert config['read_config'](config['config_path'](tmp_path)) == saved


def test_unknown_or_duplicate_assignments_rejected(tmp_path):
    path = tmp_path / 'config'
    for text in ('PATH=/evil\n', 'HERMES_UPDATE_PYTHON=on\nHERMES_UPDATE_PYTHON=off\n'):
        path.write_text(text)
        with pytest.raises(ValueError):
            config['read_config'](path)


def test_schedule_validation_precedes_writes(tmp_path):
    result = cli(tmp_path, 'schedule', 'this is not a calendar')
    assert result.returncode == 78
    assert not config['config_path'](tmp_path).exists()


def test_custom_paths_are_escaped_in_service_dropin(tmp_path):
    settings = config['effective']({'HERMES_UPDATE_REPO': str(tmp_path / 'custom % repo')}, home=tmp_path, environ={})
    config['write_units'](tmp_path, settings)
    text = (tmp_path / '.config/systemd/user/hermes-weekly-update.service.d/paths.conf').read_text()
    assert 'custom %% repo' in text
    assert 'ReadWritePaths=' in text


def test_direct_run_receives_config_without_shell_evaluation(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    for name in ('hermes-updates', 'hermes-update-config'):
        (package / name).write_bytes((ROOT / 'bin' / name).read_bytes())
        (package / name).chmod(0o755)
    controller = package / 'hermes-weekly-update'
    controller.write_text('#!/usr/bin/env python3\nimport os,sys\nprint(os.environ["HERMES_UPDATE_PYTHON"], os.environ["HERMES_UPDATE_CONFIG_LOADED"], sys.argv[1])\n')
    controller.chmod(0o755)
    config['write_config'](config['config_path'](tmp_path), {'HERMES_UPDATE_PYTHON': 'off'})
    result = subprocess.run([str(package / 'hermes-updates'), 'run', '--direct', '--check'], env={**os.environ, 'HOME': str(tmp_path)}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'off 1 --check'


def fake_command(tmp_path, name):
    directory = tmp_path / 'commands'
    directory.mkdir(exist_ok=True)
    command = directory / name
    command.write_text('#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["COMMAND_LOG"], "a") as log: log.write(json.dumps(sys.argv[1:]) + "\\n")\nif sys.argv[1:3] == ["--user", "show"]: print("ActiveState=active\\nUnitFileState=enabled")\n')
    command.chmod(0o755)
    return {'PATH': str(directory) + ':' + os.environ['PATH'], 'COMMAND_LOG': str(tmp_path / 'commands.jsonl')}


def test_schedule_changes_timer_and_on_demand_disables_it(tmp_path):
    import json
    environment = fake_command(tmp_path, 'systemctl')
    result = cli(tmp_path, 'schedule', 'daily', extra_env=environment)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (tmp_path / 'commands.jsonl').read_text().splitlines()]
    assert ['--user', 'enable', '--now', 'hermes-weekly-update.timer'] in calls
    assert ['--user', 'restart', 'hermes-weekly-update.timer'] in calls
    result = cli(tmp_path, 'schedule', 'on-demand', extra_env=environment)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (tmp_path / 'commands.jsonl').read_text().splitlines()]
    assert ['--user', 'disable', '--now', 'hermes-weekly-update.timer'] in calls
    assert config['read_config'](config['config_path'](tmp_path))['HERMES_UPDATE_SCHEDULE'] == 'on-demand'


def test_failed_systemd_apply_restores_configuration_and_dropins(tmp_path):
    result = cli(tmp_path, 'configure', '--gateway', 'off', '--no-systemd')
    assert result.returncode == 0, result.stderr
    path = config['config_path'](tmp_path)
    before = path.read_bytes()
    environment = fake_command(tmp_path, 'systemctl')
    command = Path(environment['PATH'].split(':', 1)[0]) / 'systemctl'
    command.write_text('''#!/usr/bin/env python3
import sys
if sys.argv[-1] == 'daemon-reload':
    sys.exit(1)
''')
    command.chmod(0o755)

    result = cli(tmp_path, 'configure', '--gateway', 'on', extra_env=environment)

    assert result.returncode == 78
    assert path.read_bytes() == before
    assert not (tmp_path / '.config/systemd/user/hermes-weekly-update.service.d/paths.conf').exists()


def test_failed_timer_restart_restores_prior_schedule(tmp_path):
    result = cli(tmp_path, 'schedule', 'daily', extra_env=fake_command(tmp_path, 'systemctl'))
    assert result.returncode == 0, result.stderr
    path = config['config_path'](tmp_path)
    before = path.read_bytes()
    schedule = tmp_path / '.config/systemd/user/hermes-weekly-update.timer.d/schedule.conf'
    before_schedule = schedule.read_bytes()
    command = tmp_path / 'commands/systemctl'
    command.write_text('''#!/usr/bin/env python3
import sys
if sys.argv[-2:] == ['restart', 'hermes-weekly-update.timer']:
    sys.exit(1)
if sys.argv[1:3] == ['--user', 'show']:
    print('ActiveState=active\\nUnitFileState=enabled')
''')
    command.chmod(0o755)

    result = cli(tmp_path, 'schedule', 'weekly', extra_env={'PATH': str(command.parent) + ':' + os.environ['PATH']})

    assert result.returncode == 78
    assert path.read_bytes() == before
    assert schedule.read_bytes() == before_schedule


def test_failed_schedule_preserves_disabled_inactive_timer(tmp_path):
    import json
    command = tmp_path / 'systemctl'
    log = tmp_path / 'systemctl.jsonl'
    command.write_text(f'''#!/usr/bin/env python3
import json,sys
with open({str(log)!r}, 'a') as output: output.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['--user', 'show']:
    print('ActiveState=inactive\\nUnitFileState=disabled')
elif sys.argv[-2:] == ['restart', 'hermes-weekly-update.timer']:
    sys.exit(1)
''')
    command.chmod(0o755)
    environment = {'PATH': str(tmp_path) + ':' + os.environ['PATH']}

    result = cli(tmp_path, 'schedule', 'weekly', extra_env=environment)

    assert result.returncode == 78
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ['--user', 'disable', 'hermes-weekly-update.timer'] in calls
    assert ['--user', 'stop', 'hermes-weekly-update.timer'] in calls
    assert ['--user', 'enable', '--now', 'hermes-weekly-update.timer'] in calls


def test_incomplete_file_restore_does_not_apply_prior_timer_state(tmp_path, monkeypatch, capsys):
    path = config['config_path'](tmp_path)
    old = config['effective']({'HERMES_UPDATE_SCHEDULE': 'daily'}, home=tmp_path, environ={})
    updated = config['effective']({'HERMES_UPDATE_SCHEDULE': 'weekly'}, home=tmp_path, environ={})
    module = config['save_settings'].__globals__
    applied = []

    monkeypatch.setitem(module, 'write_units', lambda home, settings: None)
    monkeypatch.setitem(module, 'restore_files', lambda saved: [(path, OSError('restore failed'))])
    monkeypatch.setitem(module, 'timer_state', lambda: ('disabled', False))
    def fail_new_schedule(settings):
        applied.append(settings)
        raise subprocess.CalledProcessError(1, 'systemctl')

    monkeypatch.setitem(module, 'apply_schedule', fail_new_schedule)
    with pytest.raises(subprocess.CalledProcessError):
        config['save_settings'](path, tmp_path, {}, updated, True, True)

    assert applied == [updated]
    assert 'RECOVERY INCOMPLETE' in capsys.readouterr().err


def test_restore_files_continues_after_one_restore_failure(tmp_path, monkeypatch):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    original_atomic_write = config['atomic_write']

    def fail_first(path, content):
        if path == first:
            raise OSError('first restore failed')
        original_atomic_write(path, content)

    monkeypatch.setitem(config['restore_files'].__globals__, 'atomic_write', fail_first)
    errors = config['restore_files']({first: b'first', second: b'second'})

    assert len(errors) == 1
    assert errors[0][0] == first
    assert 'first restore failed' in str(errors[0][1])
    assert second.read_bytes() == b'second'


@pytest.mark.parametrize('properties', [
    'ActiveState=active\nUnitFileState=enabled\n',
    'UnitFileState=enabled\nActiveState=active\n',
])
def test_timer_snapshot_uses_property_names_not_output_order(monkeypatch, properties):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=properties))
    assert config['timer_state']() == ('enabled', True)


@pytest.mark.parametrize('properties', [
    'ActiveState=active\n',
    'ActiveState=activating\nUnitFileState=enabled\n',
    'ActiveState=inactive\nUnitFileState=masked\n',
])
def test_unknown_or_changing_timer_state_stops_before_writes(monkeypatch, properties):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=properties))
    with pytest.raises(ValueError, match='timer'):
        config['timer_state']()


def test_runtime_only_timer_enablement_is_not_made_persistent(monkeypatch):
    calls = []
    monkeypatch.setitem(config['restore_timer'].__globals__, 'systemctl', lambda *args: calls.append(args))
    config['restore_timer']('enabled-runtime', False)
    assert calls == [('disable', 'hermes-weekly-update.timer'),
                     ('enable', '--runtime', 'hermes-weekly-update.timer'),
                     ('stop', 'hermes-weekly-update.timer')]


def test_partial_dropin_write_restores_prior_files(tmp_path, monkeypatch):
    path = config['config_path'](tmp_path)
    old = config['effective']({'HERMES_UPDATE_GATEWAY': 'off'}, home=tmp_path, environ={})
    proposed = {'HERMES_UPDATE_GATEWAY': 'on'}
    updated = config['effective'](proposed, home=tmp_path, environ={})
    config['write_config'](path, {'HERMES_UPDATE_GATEWAY': 'off'})
    before = path.read_bytes()
    original_write_units = config['write_units']

    def write_some_units_then_fail(home, settings):
        original_write_units(home, settings)
        raise OSError('simulated drop-in write failure')

    module = config['save_settings'].__globals__
    monkeypatch.setitem(module, 'write_units', write_some_units_then_fail)
    monkeypatch.setitem(module, 'systemctl', lambda *arguments: None)
    with pytest.raises(OSError, match='simulated'):
        config['save_settings'](path, tmp_path, proposed, updated, True, False)

    assert path.read_bytes() == before
    assert not (tmp_path / '.config/systemd/user/hermes-weekly-update.service.d/paths.conf').exists()


def test_manual_configuration_does_not_read_unusable_systemd_files(tmp_path):
    unused = tmp_path / '.config/systemd/user/hermes-weekly-update.service.d/paths.conf'
    unused.mkdir(parents=True)
    result = cli(tmp_path, 'configure', '--gateway', 'off', '--no-systemd')
    assert result.returncode == 0, result.stdout + result.stderr
    assert unused.is_dir()
    assert config['read_config'](config['config_path'](tmp_path))['HERMES_UPDATE_GATEWAY'] == 'off'


def test_bounded_run_passes_effective_settings_and_check_flag(tmp_path):
    import json
    environment = fake_command(tmp_path, 'systemd-run')
    environment['HERMES_UPDATE_PYTHON'] = 'off'
    result = cli(tmp_path, 'run', '--check', extra_env=environment)
    assert result.returncode == 0, result.stderr
    call = json.loads((tmp_path / 'commands.jsonl').read_text())
    assert '--property=MemoryMax=8G' in call
    assert '--property=RuntimeMaxSec=4h' in call
    assert '--setenv=HERMES_UPDATE_PYTHON=off' in call
    assert '--setenv=HERMES_UPDATE_CONFIG_LOADED=1' in call
    assert call[-1] == '--check'


def test_runtime_path_follows_configured_installation(tmp_path):
    settings = config['effective']({'HERMES_UPDATE_HOME': str(tmp_path / 'custom data'),
                                    'HERMES_UPDATE_REPO': str(tmp_path / 'custom source')}, home=tmp_path, environ={})
    environment = config['runtime_environment'](settings, environ={'PATH': '/usr/bin:/bin'})
    assert environment['PATH'].split(':') == [str(tmp_path / 'custom source/venv/bin'),
                                              str(tmp_path / 'custom data/node/bin'),
                                              str(tmp_path / 'custom data/bin'), '/usr/bin', '/bin']


def test_recovery_condition_is_parsed_and_matches_only_pending_state(tmp_path):
    import shutil
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze is unavailable')
    state = tmp_path / 'state % [literal]'
    state.mkdir()
    settings = config['effective']({'HERMES_UPDATE_STATE_DIR': str(state)}, home=tmp_path, environ={})
    config['write_units'](tmp_path, settings)
    unit = tmp_path / '.config/systemd/user/hermes-weekly-update-recovery.service.d/paths.conf'
    condition = unit.read_text().splitlines()[-1]
    empty = subprocess.run(['systemd-analyze', 'condition', condition], text=True, capture_output=True)
    assert empty.returncode != 0
    (state / 'promotion-pending').touch()
    pending = subprocess.run(['systemd-analyze', 'condition', condition], text=True, capture_output=True)
    assert pending.returncode == 0, pending.stdout + pending.stderr
