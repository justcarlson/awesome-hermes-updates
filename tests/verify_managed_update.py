"""Check a completed local managed update without changing source or services."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen


def command(*arguments, cwd=None):
    return subprocess.check_output(arguments, cwd=cwd, text=True, timeout=30,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                                        'HERMES_DISABLE_LAZY_INSTALLS': '1'}).strip()


def valid_baseline(before, profiles):
    if not isinstance(before, dict):
        return False
    installed_sha = before.get('installed_sha')
    if not isinstance(installed_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', installed_sha):
        return False
    dashboard = before.get('dashboard')
    if not isinstance(dashboard, dict):
        return False
    dashboard_pid = dashboard.get('pid')
    if not isinstance(dashboard_pid, int) or isinstance(dashboard_pid, bool) or dashboard_pid <= 0:
        return False
    gateways = before.get('gateways')
    if not isinstance(gateways, dict):
        return False
    for profile in profiles:
        gateway = gateways.get(profile)
        if not isinstance(gateway, dict):
            return False
        platform_states = gateway.get('platform_states')
        if not isinstance(platform_states, dict) or not all(
                isinstance(name, str) and isinstance(state, str)
                for name, state in platform_states.items()):
            return False
    return True


def observe(args):
    checks = {'baseline_available': args.baseline_report is not None}
    details = {'target': args.target}
    properties = dict(line.split('=', 1) for line in command(
        'systemctl', '--user', 'show', 'hermes-weekly-update.service',
        '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus',
        '-p', 'MemoryPeak', '-p', 'CPUUsageNSec').splitlines())
    details['service'] = properties
    checks['terminal_service_success'] = (properties['ActiveState'] == 'inactive'
                                          and properties['Result'] == 'success'
                                          and properties['ExecMainStatus'] == '0')
    checks['no_pending_state'] = not any((args.state / p).exists()
                                        for p in ('repair-pending', 'promotion-pending'))
    receipt = dict(line.split('=', 1) for line in (args.state / 'last-result').read_text().splitlines())
    checks['successful_target_receipt'] = receipt.get('exit_status') == '0' and receipt.get('target') == args.target
    head = command('git', '-C', str(args.repo), 'rev-parse', 'HEAD')
    details['installed_sha'] = head
    checks['target_in_production'] = subprocess.run(
        ['git', '-C', str(args.repo), 'merge-base', '--is-ancestor', args.target, head],
        timeout=10, capture_output=True).returncode == 0
    checks['production_clean'] = not command('git', '-C', str(args.repo), 'status', '--porcelain')
    details['gateways'] = {}
    for profile in args.profile:
        unit = 'hermes-gateway.service' if profile == 'default' else f'hermes-gateway-{profile}.service'
        home = args.home if profile == 'default' else args.home / 'profiles' / profile
        pid = int(command('systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'))
        state = json.loads((home / 'gateway_state.json').read_text())
        details['gateways'][profile] = {key: state.get(key) for key in ('pid', 'gateway_state', 'code_sha')}
        details['gateways'][profile]['platform_states'] = {
            name: value.get('state') for name, value in state.get('platforms', {}).items()}
        checks[f'gateway_{profile}'] = (command('systemctl', '--user', 'is-active', unit) == 'active'
                                         and pid > 0 and state.get('pid') == pid
                                         and state.get('gateway_state') == 'running'
                                         and state.get('code_sha') == head)
    checks['dashboard_active'] = command('systemctl', '--user', 'is-active', 'hermes-dashboard.service') == 'active'
    dashboard_pid = int(command('systemctl', '--user', 'show', 'hermes-dashboard.service', '-p', 'MainPID', '--value'))
    with urlopen(args.dashboard_url.rstrip('/') + '/api/health', timeout=10) as response:
        health = json.load(response)
    checks['dashboard_http_health'] = health.get('ok') is True
    with urlopen(args.dashboard_url.rstrip('/') + '/api/status', timeout=10) as response:
        status = json.load(response)
    components = status.get('components', {})
    details['dashboard'] = {'pid': dashboard_pid, 'health': health, 'overall': status.get('overall'),
                            'components': components,
                            'platform_states': {name: value.get('state') for name, value in status.get('gateway_platforms', {}).items()}}
    checks['dashboard_application_status'] = all(components.get(name, {}).get('status') == 'ok'
                                                 for name in ('gateway', 'dashboard', 'storage'))
    version = command(str(args.repo / 'venv/bin/python'), '-m', 'hermes_cli.main', '--version', cwd=args.repo)
    details['version'] = version
    local_sha = re.search(r'\blocal ([0-9a-f]{8,40})\b', version)
    checks['version_matches'] = (local_sha is not None and head.startswith(local_sha[1])
                                 and f"Hermes Agent v{health.get('version')}" in version)
    runtime = command(sys.executable, str(Path.home() / '.local/bin/hermes-update-state'),
                      'runtime-check', str(args.repo))
    details['runtime'] = runtime
    checks['safe_sqlite_runtime'] = 'safe=True' in runtime
    if args.baseline_report:
        before = json.loads(args.baseline_report.read_text())['details']
        checks['baseline_evidence_valid'] = valid_baseline(before, args.profile)
        if checks['baseline_evidence_valid']:
            if before['installed_sha'] != head:
                checks['dashboard_restarted'] = dashboard_pid > 0 and dashboard_pid != before['dashboard']['pid']
            for profile in args.profile:
                old_states = before['gateways'][profile]['platform_states']
                new_states = details['gateways'][profile]['platform_states']
                checks[f'connections_preserved_{profile}'] = all(
                    new_states.get(name) == 'connected' for name, state in old_states.items() if state == 'connected')
    checks['timer_enabled'] = command('systemctl', '--user', 'is-enabled', 'hermes-weekly-update.timer') == 'enabled'
    return {'checks': checks, 'details': details,
            'limits': 'This checks source, processes, receipts, and public dashboard APIs. It does not test provider requests or authenticated chat.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True, help='Hermes data home, not the operating-system user home')
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--dashboard-url', required=True)
    parser.add_argument('--profile', action='append', required=True,
                        help='selected gateway profile; use default for the main home')
    parser.add_argument('--baseline-report', type=Path,
                        help='required for a passing proof; omit only to capture baseline observations')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.target):
        parser.error('--target must be a full commit SHA')
    if not all(re.fullmatch('[A-Za-z0-9_-]+', p) for p in args.profile):
        parser.error('--profile must contain only letters, digits, underscores, and hyphens')
    if args.baseline_report and args.baseline_report.resolve() == args.output.resolve():
        parser.error('--output must not replace the baseline report')
    try:
        report = observe(args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        report = {'checks': {'observation_completed': False}, 'error': str(error)}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
