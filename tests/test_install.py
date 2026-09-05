"""Installation must preserve local settings and pending recovery state."""
import fcntl
from pathlib import Path
import subprocess

import pytest

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


@pytest.mark.parametrize('clients, directories', [
    ('codex', ['.agents']), ('claude', ['.claude']), ('both', ['.agents', '.claude']),
])
def test_skill_selection_survives_reinstall(tmp_path, clients, directories):
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--skills', clients], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    current = tmp_path / '.local/share/awesome-hermes-updates/current'
    generation = current.resolve()
    for directory in directories:
        skill = tmp_path / directory / 'skills/update-hermes-agent'
        assert skill.is_symlink()
        assert skill.resolve() == generation / 'skills/update-hermes-agent'
        assert (skill / 'references/recovery.md').is_file()
        assert (skill / 'agents/openai.yaml').is_file()
    assert install(tmp_path).returncode == 0
    assert current.resolve() == generation
    assert (current.parent / 'skill-clients').read_text() == clients + '\n'
    for directory in directories:
        assert (tmp_path / directory / 'skills/update-hermes-agent/SKILL.md').is_file()


def test_default_install_does_not_register_skills(tmp_path):
    assert install(tmp_path).returncode == 0
    for directory in ('.agents', '.claude'):
        assert not (tmp_path / directory / 'skills/update-hermes-agent').exists()


@pytest.mark.parametrize('conflict', ['directory', 'symlink'])
def test_skill_collision_preserves_user_copy_and_current_generation(tmp_path, conflict):
    assert install(tmp_path).returncode == 0
    current = tmp_path / '.local/share/awesome-hermes-updates/current'
    generation = current.resolve()
    skill = tmp_path / '.claude/skills/update-hermes-agent'
    skill.parent.mkdir(parents=True, exist_ok=True)
    if conflict == 'symlink':
        skill.symlink_to(tmp_path / 'missing-user-skill')
    else:
        skill.mkdir()
        (skill / 'SKILL.md').write_text('user edits')
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--skills', 'both'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'not managed by this installer' in result.stderr
    assert current.resolve() == generation
    assert not (tmp_path / '.agents/skills/update-hermes-agent').exists()
    assert (current.parent / 'skill-clients').read_text() == 'none\n'
    if conflict == 'symlink':
        assert skill.readlink() == tmp_path / 'missing-user-skill'
    else:
        assert (skill / 'SKILL.md').read_text() == 'user edits'


def test_deselect_skills_removes_only_owned_links(tmp_path):
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--skills', 'both'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    claude = tmp_path / '.claude/skills/update-hermes-agent'
    claude.unlink()
    claude.mkdir()
    (claude / 'SKILL.md').write_text('user replacement')
    result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--skills', 'none'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    codex = tmp_path / '.agents/skills/update-hermes-agent'
    assert not codex.exists() and not codex.is_symlink()
    assert (claude / 'SKILL.md').read_text() == 'user replacement'


def test_install_rejects_unsupported_platform_before_writing(tmp_path, monkeypatch):
    import runpy
    import sys

    installer = runpy.run_path(str(ROOT / 'install'))
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(sys, 'argv', ['install', '--stage', str(tmp_path)])
    with pytest.raises(SystemExit, match='2'):
        installer['main']()
    assert list(tmp_path.iterdir()) == []


def test_skill_links_follow_new_generation_without_losing_previous_release(tmp_path):
    import shutil

    source = tmp_path / 'package'
    source.mkdir()
    for filename in ('install', 'README.md'):
        shutil.copy2(ROOT / filename, source / filename)
    for directory in ('bin', 'systemd', 'skills', 'docs', '.codex-plugin', '.claude-plugin'):
        shutil.copytree(ROOT / directory, source / directory,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    home = tmp_path / 'home'
    command = [str(source / 'install'), '--stage', str(home)]
    result = subprocess.run([*command, '--skills', 'both'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    current = home / '.local/share/awesome-hermes-updates/current'
    old = current.resolve()
    skill_file = Path('skills/update-hermes-agent/SKILL.md')
    original = (source / skill_file).read_text()
    (source / skill_file).write_text(original + '\nUpdated package instructions.\n')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert current.resolve() != old
    assert (old / skill_file).read_text() == original
    for client in ('.agents', '.claude'):
        installed = home / client / skill_file
        assert installed.read_text() == (source / skill_file).read_text()
    for manifest in ('.codex-plugin/plugin.json', '.claude-plugin/plugin.json'):
        assert (current / manifest).read_bytes() == (source / manifest).read_bytes()


@pytest.mark.parametrize('selected', ['codex', 'claude'])
def test_shared_client_store_keeps_selected_skill(tmp_path, selected):
    codex = tmp_path / '.agents/skills'
    codex.mkdir(parents=True)
    claude = tmp_path / '.claude/skills'
    claude.parent.mkdir()
    claude.symlink_to(codex, target_is_directory=True)
    for selection in ('both', selected):
        result = subprocess.run([str(ROOT / 'install'), '--stage', str(tmp_path), '--skills', selection], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (codex / 'update-hermes-agent/SKILL.md').is_file()
        assert (claude / 'update-hermes-agent/SKILL.md').is_file()
