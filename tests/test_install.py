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
