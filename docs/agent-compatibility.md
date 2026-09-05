# Agent compatibility

Verified against official documentation on 2026-09-05. The updater runs on Linux;
Codex and Claude Code are optional operators and optional source-repair backends.
Installing a skill does not enable repairs, schedule an update, or install Hermes.

## Shared skill

Both clients consume the same `skills/update-hermes-agent/SKILL.md`, with YAML
`name` and `description`. Recovery instructions are a relative reference inside
that directory, so copying or linking the complete directory keeps them available.
Codex's `agents/openai.yaml` adds optional display and invocation metadata.
**Standalone skills require no JSON manifest.** These requirements and Codex's
symlink support are documented in [OpenAI's skill guide](https://learn.chatgpt.com/docs/build-skills).
Claude's discovery paths and frontmatter are documented in
[Claude Code's skill guide](https://code.claude.com/docs/en/skills).

| Installation | Codex | Claude Code |
| --- | --- | --- |
| User skill | `~/.agents/skills/update-hermes-agent/` | `~/.claude/skills/update-hermes-agent/` |
| Project skill | `.agents/skills/update-hermes-agent/` | `.claude/skills/update-hermes-agent/` |
| Native plugin manifest | `.codex-plugin/plugin.json` | `.claude-plugin/plugin.json` |

`./install --skills codex`, `claude`, or `both` manages user skill links through
the immutable installed generation. Repeat installation preserves the selected
clients, updater settings, schedule, and recovery evidence. `--skills none`
removes only installer-owned links. Existing user copies cause a conflict before
the installer switches generations; they are never overwritten.

## Native plugins

The repository includes two small manifests and one shared `skills/` directory.
Codex requires `.codex-plugin/plugin.json`; its `skills` field points to
`./skills/`. See [OpenAI's plugin guide](https://learn.chatgpt.com/docs/build-plugins).
Claude uses `.claude-plugin/plugin.json` for identity and discovers `skills/`
by convention. See [Claude's plugin reference](https://code.claude.com/docs/en/plugins-reference).
No MCP server or marketplace registration is needed for the standalone skill.

For a local Claude plugin session, from the updater checkout:

```sh
claude plugin validate .claude-plugin/plugin.json
claude --plugin-dir "$PWD"
```

The plugin skill is namespaced as
`/awesome-hermes-updates:update-hermes-agent`. Local plugin loading uses Claude's
[documented development flow](https://code.claude.com/docs/en/plugins).
For Codex, use the standalone installer above, or register this package in your
own local marketplace using the OpenAI plugin guide. This repository does not
publish a marketplace listing. Avoid loading both standalone and plugin copies
in the same client.

## Optional repair clients

`--repair-agent codex|claude` chooses the headless CLI; `--max-repairs 0` disables
source repairs regardless of the choice. Existing configurations default to
Codex. Both backends use the same candidate, timeout, persistent attempt count,
and post-repair verification. Dependency and toolchain failures stop before
source repair. The agent operating the skill can differ from the selected repair
backend.

Codex uses `exec` with a workspace sandbox, ephemeral session, medium reasoning,
and JSON logs. Claude uses print mode with streamed JSON, medium effort, no
session persistence, and a required native Bash sandbox. Its tool set excludes
subagents; ambient hooks, skills, and MCP servers are disabled. Claude repairs
require version **2.1.246 or newer**, Bubblewrap, socat, and authentication completed
before an unattended run. That version is needed for excluded settings sources
to stay excluded from sandbox filesystem permissions. See
[Claude automation](https://code.claude.com/docs/en/headless) and
[Claude sandbox configuration](https://code.claude.com/docs/en/sandboxing).

The updater sets `CLAUDE_CONFIG_DIR="$HOME/.claude"` for repairs so global config,
locks, and temporary config files stay inside the service's writable Claude
directory. Credentials remain in that directory. Complete setup with
`CLAUDE_CONFIG_DIR="$HOME/.claude" claude auth login` before unattended use.
See [Claude's configuration directories](https://code.claude.com/docs/en/claude-directory).

The regression suite uses fake client executables to verify invocation, limits,
and candidate promotion without model charges. Native Claude plugin validation
also runs without a model call. A real authenticated model repair was not part
of this compatibility validation.

## Writing reference

The skill follows Matt Pocock's
[writing-for-agents](https://github.com/mattpocock/skills/blob/main/skills/productivity/writing-for-agents/SKILL.md):
keep one workflow, explicit completion criteria, and recovery behind a conditional
reference. His [wizard](https://github.com/mattpocock/skills/blob/main/skills/engineering/wizard/SKILL.md)
was reviewed; it targets steps only a human can perform, so the updater retains
its noninteractive configuration commands. These skills informed the structure;
they are not runtime dependencies or bundled copies.
