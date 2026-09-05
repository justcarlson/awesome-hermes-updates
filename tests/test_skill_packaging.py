"""Both assistant packages must discover the same self-contained operator skill."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/update-hermes-agent'


def test_native_plugins_share_identity_and_skill_directory():
    codex = json.loads((ROOT / '.codex-plugin/plugin.json').read_text())
    claude = json.loads((ROOT / '.claude-plugin/plugin.json').read_text())
    for key in ('name', 'version', 'description'):
        assert codex[key] == claude[key]
        assert isinstance(codex[key], str) and codex[key].strip()
    assert re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', codex['name'])
    assert re.fullmatch(r'\d+\.\d+\.\d+', codex['version'])
    assert codex['skills'].startswith('./')
    assert (ROOT / codex['skills']).resolve() == SKILL.parent.resolve()
    # Claude discovers skills/ by default when no custom path is declared.
    assert 'skills' not in claude
    assert list(SKILL.parent.glob('*/SKILL.md')) == [SKILL / 'SKILL.md']


def test_shared_skill_has_portable_metadata_and_resolvable_references():
    content = (SKILL / 'SKILL.md').read_text()
    header = re.match(r'\A---\n(.*?)\n---\n', content, re.DOTALL)
    assert header, 'SKILL.md must begin with YAML frontmatter'
    # The shared metadata intentionally uses simple single-line YAML scalars.
    fields = dict(re.findall(r'^([a-z-]+): (.+)$', header[1], re.MULTILINE))
    assert fields['name'] == SKILL.name
    assert 0 < len(fields['description']) <= 1024
    assert not fields['description'].startswith(('>', '|'))
    body = content[header.end():]
    links = re.findall(r'\[[^\]]+\]\(([^)]+)\)', body)
    assert 'references/recovery.md' in links
    for target in links:
        if '://' in target or target.startswith('#'):
            continue
        path = (SKILL / target.split('#', 1)[0]).resolve()
        assert path.is_relative_to(SKILL.resolve()), target
        assert path.is_file(), target


def test_codex_ui_metadata_names_the_shared_skill():
    metadata = (SKILL / 'agents/openai.yaml').read_text()
    assert re.search(r'^interface:\s*$', metadata, re.MULTILINE)
    for key in ('display_name', 'short_description', 'default_prompt'):
        match = re.search(rf'^  {key}: (".*")$', metadata, re.MULTILINE)
        assert match, f'{key} must be a quoted, single-line UI string'
        value = json.loads(match[1])
        assert isinstance(value, str) and value.strip()
        if key == 'default_prompt':
            assert f'${SKILL.name}' in value
