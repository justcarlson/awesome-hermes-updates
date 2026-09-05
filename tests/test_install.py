"""Installation must preserve local settings and pending recovery state."""
import fcntl
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def install(home):
    return subprocess.run([str(ROOT / 'install'), '--stage', str(home)], capture_output=True, text=True)


def test_repeat_install_preserves_settings_and_pending_state(tmp_path):
    assert install(tmp_path).returncode == 0
    current = tmp_path / '.local/share/awesome-hermes-updates/current'
    generation = current.resolve()
    config = tmp_path / '.config/awesome-hermes-updates/config'
    config.write_text('HERMES_UPDATE_MAX_REPAIRS=0\n')
    pending = tmp_path / '.local/state/hermes-weekly-update/repair-pending'
    pending.write_text('preserve evidence\n')
    assert install(tmp_path).returncode == 0
    assert current.resolve() == generation
    assert not (generation / 'bin/__pycache__').exists()
    assert config.read_text() == 'HERMES_UPDATE_MAX_REPAIRS=0\n'
    assert pending.read_text() == 'preserve evidence\n'
    for name in ('hermes-weekly-update', 'hermes-update-state'):
        assert (tmp_path / '.local/bin' / name).read_bytes() == (ROOT / 'bin' / name).read_bytes()


def test_running_update_blocks_install(tmp_path):
    state = tmp_path / '.local/state/hermes-weekly-update'
    state.mkdir(parents=True)
    with (state / 'update.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = install(tmp_path)
    assert result.returncode != 0
    assert 'an update is running' in result.stderr
    assert not (tmp_path / '.local/share/awesome-hermes-updates/current').exists()


def test_install_without_systemd_or_hermes_setup(tmp_path):
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--no-systemd', '--repo', str(tmp_path / 'source')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / '.local/bin/hermes-updates').exists()
    assert not (tmp_path / '.config/systemd/user').exists()
    assert 'source' in (tmp_path / '.config/awesome-hermes-updates/config').read_text()


def test_invalid_install_setting_leaves_no_release(tmp_path):
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--repo', 'relative'], capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / '.local/share/awesome-hermes-updates/current').exists()


def test_custom_paths_and_calendar_survive_reinstall(tmp_path):
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--repo', str(tmp_path / 'custom repo'), '--schedule', 'daily'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calendar = tmp_path / '.config/systemd/user/hermes-weekly-update.timer.d/schedule.conf'
    before = calendar.read_text()
    assert install(tmp_path).returncode == 0
    assert calendar.read_text() == before
    paths = tmp_path / '.config/systemd/user/hermes-weekly-update.service.d/paths.conf'
    assert 'custom repo' in paths.read_text()


def test_legacy_calendar_is_imported_without_changing_pending_state(tmp_path):
    units = tmp_path / '.config/systemd/user'
    units.mkdir(parents=True)
    (units / 'hermes-weekly-update.timer').write_text('[Timer]\nOnCalendar=Sun *-*-* 03:15:00 America/New_York\n')
    state = tmp_path / '.local/state/hermes-weekly-update'
    state.mkdir(parents=True)
    (state / 'promotion-pending').write_text('preserve')
    result = install(tmp_path)
    assert result.returncode == 0, result.stderr
    config = tmp_path / '.config/awesome-hermes-updates/config'
    assert 'Sun *-*-* 03:15:00 America/New_York' in config.read_text()
    assert 'Sun *-*-* 03:15:00 America/New_York' in (units / 'hermes-weekly-update.timer.d/schedule.conf').read_text()
    assert (state / 'promotion-pending').read_text() == 'preserve'


def test_staged_installed_cli_plans_unconfigured_checkout(tmp_path):
    import json
    import os

    repo = tmp_path / 'source checkout'
    subprocess.run(['git', 'init', '--quiet', str(repo)], check=True)
    (repo / 'README.md').write_text('Minimal Hermes checkout fixture\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'README.md'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--quiet', '-m', 'initial'], check=True)
    before = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True)
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'systemctl').write_text('#!/bin/sh\nexit 1\n')
    (tools / 'systemctl').chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith('HERMES_UPDATE_') and key != 'HERMES_HOME'}
    environment.update(HOME=str(tmp_path), PATH=str(tools) + ':' + os.environ['PATH'])
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--repo', str(repo), '--hermes-home', str(tmp_path / 'unconfigured home')], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    cli = tmp_path / '.local/bin/hermes-updates'
    result = subprocess.run([str(cli), 'config'], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    settings = json.loads(result.stdout)
    assert settings['HERMES_UPDATE_REPO'] == str(repo)
    assert settings['HERMES_UPDATE_HOME'] == str(tmp_path / 'unconfigured home')
    assert settings['HERMES_UPDATE_SCHEDULE'] == 'on-demand'
    result = subprocess.run([str(cli), 'plan'], env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'Resolved actions: python=0 node=0 dashboard=0 gateway=0 migrate=0 runtime=0' in result.stdout
    assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True) == before
    assert not subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True)
    assert not (tmp_path / 'unconfigured home').exists()
    assert not (tmp_path / '.local/state/hermes-weekly-update/deployment-profiles').exists()
    assert not (tmp_path / '.local/state/hermes-weekly-update/promotion-pending').exists()


def test_legacy_override_calendar_is_preserved(tmp_path):
    units = tmp_path / '.config/systemd/user'
    units.mkdir(parents=True)
    (units / 'hermes-weekly-update.timer').write_text('[Timer]\nOnCalendar=Sun *-*-* 03:15:00 America/New_York\n')
    dropins = units / 'hermes-weekly-update.timer.d'
    dropins.mkdir()
    (dropins / 'override.conf').write_text('[Timer]\nOnCalendar=\nOnCalendar=Mon *-*-* 09:00:00 UTC\n')
    result = install(tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'Mon *-*-* 09:00:00 UTC' in (tmp_path / '.config/awesome-hermes-updates/config').read_text()
    assert 'Mon *-*-* 09:00:00 UTC' in (dropins / 'schedule.conf').read_text()
    assert 'Sun ' not in (dropins / 'schedule.conf').read_text()


def test_install_prepares_missing_service_cache_mounts(tmp_path):
    result = install(tmp_path)
    assert result.returncode == 0, result.stderr
    for name in ('.cache', '.npm', '.codex', '.local/state'):
        assert (tmp_path / name).is_dir()
