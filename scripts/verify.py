#!/usr/bin/env python3
"""Run each test module with a deadline and retain fresh, attributable evidence.

No dependency downloads, credentials or cloud calls. --desktop runs existing safe
native fixtures, but deliberately does not approve GNOME portal dialogs.
"""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_buddy.diagnostics import collect,screen_activity_probe


def source_fingerprint():
    digest = hashlib.sha256()
    paths = [ROOT / 'pyproject.toml', *sorted((ROOT / 'desktop_buddy').rglob('*.py')),
             *sorted((ROOT / 'tests').glob('*.py')), *sorted((ROOT / 'scripts').glob('*.py'))]
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def run_check(command, log, timeout):
    started = time.monotonic()
    with log.open('w') as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Kill the dedicated test group, including children left by a failed fixture.
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
    text = log.read_text(errors='replace')
    counts = re.findall(r'Ran (\d+) tests?', text)
    skipped = re.search(r'skipped=(\d+)', text)
    run_count = int(counts[-1]) if counts else None
    skip_count = int(skipped[1]) if skipped else 0
    callback_failed = 'Desktop Buddy could not start:' in text
    return {'status': 'timeout' if timed_out else 'passed' if process.returncode == 0 and not callback_failed else 'failed',
            'returncode': process.returncode, 'seconds': round(time.monotonic() - started, 3),
            'tests_run': run_count,
            'tests_passed': (run_count - skip_count if process.returncode == 0 and run_count is not None
                             else len(re.findall(r'^test_[^\n]* \.\.\. ok$', text, re.MULTILINE))),
            'tests_skipped': skip_count, 'log': str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--module', action='append', help='Test module stem, repeatable; default all')
    parser.add_argument('--timeout', type=float, default=90, help='Deadline per module (seconds)')
    args = parser.parse_args()
    if args.timeout <= 0: parser.error('--timeout must be positive')
    available = {p.stem: p for p in (ROOT / 'tests').glob('test_*.py')}
    names = args.module or sorted(available)
    if any(name not in available for name in names): parser.error('Unknown test module')
    parent = ROOT / '.test-data' / 'verification'
    parent.mkdir(parents=True, exist_ok=True)
    report_dir = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    report = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'scripted fixtures, not live-model acceptance',
              'source_sha256_start': source_fingerprint(), 'environment': collect(), 'checks': {}}
    report_path = report_dir / 'report.json'
    def save(): report_path.write_text(json.dumps(report, indent=2) + '\n')
    save()
    print('Evidence: ' + str(report_path), flush=True)
    for name in names:
        result = run_check([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                            '-p', name + '.py', '-v'], report_dir / (name + '.log'), args.timeout)
        report['checks'][name] = result
        save()
        print(f'{name}: {result["status"]} ({result["seconds"]}s)', flush=True)
    if args.desktop:
        # Prove display access independently: a native signal callback can fail
        # before a fixture timer runs without propagating a nonzero GTK exit code.
        preflight = run_check([sys.executable, '-c',
            'import gi; gi.require_version("Gtk","4.0"); from gi.repository import Gtk,Gdk; '
            'raise SystemExit(0 if Gdk.Display.open(None) is not None else 2)'], report_dir / 'desktop-display.log', 10)
        report['checks']['desktop-display'] = preflight
        save()
        scripts = ['test-computer-access-ui.py', 'test-command-review-ui.py', 'test-file-ui.py', 'test-jane-ui.py', 'test-first-use-ui.py', 'test-mcp-ui.py', 'test-yolo-ui.py', 'test-redesign-ui.py',
                   'test-model-ui.py', 'test-appearance-ui.py', 'test-theme-surfaces-ui.py', 'test-ui.py', 'test-conversation-ui.py',
                   'test-accessibility-control.py', 'test-accessible-text-ui.py', 'test-rejected-text-ui.py', 'test-pointer-target-ui.py', 'test-physical-workflow.py']
        for name in scripts:
            lock=screen_activity_probe()
            if preflight['status'] != 'passed' or lock['status']!='inactive':
                reason='No accessible native GTK display.' if preflight['status']!='passed' else 'GNOME screen-saver status is '+lock['status']+'. Focus the desktop normally before native UI checks.'
                report['checks'][name] = {'status': 'blocked', 'reason':reason+' Fixture was not launched.'}
                save()
                print(f'{name}: blocked ({reason})', flush=True)
                continue
            report['checks'][name] = run_check([sys.executable, str(ROOT / 'scripts' / name)],
                                               report_dir / (name + '.log'), args.timeout)
            save()
            print(f'{name}: {report["checks"][name]["status"]}', flush=True)
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['source_sha256_end'] = source_fingerprint()
    report['source_changed_during_run'] = report['source_sha256_end'] != report['source_sha256_start']
    report['all_checks_passed'] = (not report['source_changed_during_run'] and
                                 all(item['status'] == 'passed' for item in report['checks'].values()))
    save()
    print('Report: ' + str(report_path))
    return 0 if report['all_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
