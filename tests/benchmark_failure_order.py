"""Measure a repeated failure in fresh candidates with a copied Hermes runner.

This uses synthetic tests. It does not update Hermes or measure update success.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--helper', type=Path, default=Path(__file__).resolve().parents[1] / 'bin/hermes-update-state')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--temporary-root', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=3)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error('--samples must be positive')
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('HERMES_', 'PYTEST_'))}
    environment.update(HERMES_TEST_WORKERS='4', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    report = {'workload': 'fresh candidate with one known failing file and 24 one-second passing files',
              'python': sys.version, 'workers': 4, 'samples': args.samples,
              'runner_sha256': hashlib.sha256(args.runner.read_bytes()).hexdigest()}

    def run_case(helper, label, sample, outcome='failed'):
        with tempfile.TemporaryDirectory(prefix='ahu-order-', dir=args.temporary_root) as directory:
            root = Path(directory)
            repo, state = root / 'repo', root / 'state'
            (repo / 'scripts').mkdir(parents=True)
            (repo / 'tests').mkdir()
            state.mkdir()
            (state / 'python-failures.json').write_text('["tests/test_known.py"]')
            shutil.copy2(args.runner, repo / 'scripts/run_tests_parallel.py')
            (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\ntest_durations.json\n')
            for name in ['test_known.py', *[f'test_pass_{i:02}.py' for i in range(24)]]:
                if name == 'test_known.py' and outcome == 'empty':
                    content = ''
                else:
                    delay = 1 if name != 'test_known.py' and outcome == 'failed' else 0
                    content = ('from pathlib import Path\nimport time\n'
                               'def test_case():\n'
                               '    with Path(".git/calls").open("a") as stream:\n'
                               f'        stream.write({name!r} + "\\n")\n'
                               f'    time.sleep({delay})\n'
                               f'    assert {name != "test_known.py" or outcome != "failed"}\n')
                (repo / 'tests' / name).write_text(content)
            for command in [('init', '-q'), ('add', '.'),
                            ('-c', 'user.name=Benchmark', '-c', 'user.email=benchmark@example.invalid',
                             'commit', '-qm', 'synthetic workload')]:
                subprocess.run(['git', '-C', str(repo), *command], check=True, capture_output=True)
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(helper.resolve()), 'verify', str(repo), '--state', str(state)],
                                    cwd=repo, env=environment, capture_output=True, text=True, timeout=120)
            elapsed = time.monotonic() - started
            log = args.output.with_name(f'{args.output.stem}-{label}-{sample}.log')
            log.write_text(result.stdout + result.stderr)
            assert result.returncode == (1 if outcome == 'failed' else 0), str(log)
            assert 'run=25 cached=0' in result.stdout, str(log)
            calls = (repo / '.git/calls').read_text().splitlines()
            expected = (1 if label == 'changed' else 25) if outcome == 'failed' else (24 if outcome == 'empty' else 25)
            assert len(set(calls)) == expected, str(log)
            return {'seconds': elapsed, 'test_executions': len(calls),
                    'unique_files_executed': len(set(calls)), 'exit_status': result.returncode, 'log': str(log)}

    for label, helper in [('baseline', args.baseline), ('changed', args.helper)]:
        samples = [run_case(helper, label, sample) for sample in range(args.samples)]
        report[label] = {'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
                         'runs': samples, 'median_seconds': statistics.median(s['seconds'] for s in samples)}
    report['fixed_candidate'] = run_case(args.helper, 'fixed', 0, 'passed')
    report['empty_former_failure'] = run_case(args.helper, 'empty', 0, 'empty')
    report['median_reduction_percent'] = 100 * (1 - report['changed']['median_seconds'] / report['baseline']['median_seconds'])
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
