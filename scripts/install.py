#!/usr/bin/env python3
"""Install Buddy for this user, offline, using existing system GTK dependencies.

No sudo, dependency downloads, services, shell-profile edits, or credential copies.
An existing environment/launcher is never overwritten. --prefix and --desktop-dir
allow a completely isolated installation test.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def desktop_quote(value):
    if '\n' in value or '\r' in value:
        raise ValueError('Installation paths cannot contain newlines.')
    value = value.replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return '"' + value + '"'


def main():
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    if not data_home.is_absolute():data_home = Path.home() / '.local' / 'share'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=data_home / 'desktop-buddy')
    parser.add_argument('--desktop-dir', type=Path, default=data_home / 'applications')
    args = parser.parse_args()
    if os.geteuid() == 0:parser.error('Run as your ordinary desktop user, without sudo.')
    prefix = args.prefix.expanduser().resolve()
    environment = prefix / 'venv'
    desktop = args.desktop_dir.expanduser().resolve() / 'io.desktopbuddy.App.desktop'
    executable = environment / 'bin' / 'desktop-buddy'
    quoted = desktop_quote(str(executable))
    if environment.exists() or desktop.exists():
        parser.error('An installation or launcher already exists. Choose an unused prefix/desktop directory; existing files were not changed.')
    # Verify native dependencies before creating the installation.
    import gi
    import requests
    from PIL import Image
    for name, version in [('Gtk', '4.0'), ('Secret', '1'), ('Atspi', '2.0'), ('Gst', '1.0')]:
        gi.require_version(name, version)
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='buddy-wheel-') as temporary:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
            '--no-index', '--wheel-dir', temporary, str(repository)], check=True)
        wheel = next(Path(temporary).glob('desktop_buddy-*.whl'))
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(environment)
        subprocess.run([sys.executable, '-m', 'pip', '--python', str(environment / 'bin' / 'python'),
            'install', '--no-deps', '--no-index', str(wheel)], check=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    with desktop.open('x') as output:
        output.write('[Desktop Entry]\nType=Application\nName=Desktop Buddy\n'
            'Comment=AI assistance for your Linux desktop\nExec=' + quoted + '\n'
            'Icon=computer\nTerminal=false\nCategories=Utility;\nStartupNotify=true\n')
    print(f'Installed launcher: {desktop}')
    print(f'Run: {executable}')
    print('Existing source-checkout history was not moved. To reuse it, launch with --data-dir /path/to/checkout/.buddy after closing the source app.')


if __name__ == '__main__':main()
