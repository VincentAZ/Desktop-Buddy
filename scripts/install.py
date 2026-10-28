#!/usr/bin/env python3
"""Install or update Buddy without changing conversations, settings or sign-ins.

Builds a complete private Python environment using system GTK libraries. Downloads
are opt-in; --wheelhouse supports an offline distribution. Run without sudo.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import venv

RUNNER = '''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
root = Path(__file__).resolve().parent
try:
    name = json.loads((root / 'installation.json').read_text())['active']
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid installation path')
    python = root / relative / 'bin' / 'python'
    if not python.is_file():
        raise ValueError('Installed version is missing')
except (OSError, ValueError, KeyError, TypeError) as error:
    sys.exit('Desktop Buddy could not start: ' + str(error))
os.execv(python, [str(python), '-I', '-m', 'desktop_buddy', *sys.argv[1:]])
'''


def desktop_quote(value, *, legacy=False):
    if any(c in value for c in '\n\r\0'):
        raise ValueError('Installation paths cannot contain newlines or null characters.')
    value = value.replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    if not legacy:
        value = value.replace('\\', '\\\\').replace('\t', r'\t')
    return '"' + value + '"'


def desktop_text(executable, *, legacy=False):
    return ('[Desktop Entry]\nType=Application\nName=Desktop Buddy\n'
            'Comment=AI assistance for your Linux desktop\nExec=' + ('' if legacy else '/usr/bin/python3 ') + desktop_quote(str(executable), legacy=legacy) + '\n'
            'Icon=computer\nTerminal=false\nCategories=Utility;\nStartupNotify=true\n')


def publish(path, text, *, exclusive=False, mode=0o600):
    """Atomic replacement: an exception after replacement never undoes a commit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), mode)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        # The rename is the commit point. Failure to sync the directory must not
        # cause callers to remove an environment already selected by the launcher.
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        except OSError as error:
            print(f'Warning: could not sync installation directory: {error}', file=sys.stderr)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


@contextmanager
def installation_lock(prefix):
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(prefix / '.installation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another installation or update is running.') from None
        yield
    finally:
        os.close(fd)


def release_path(prefix, name):
    if not isinstance(name, str) or not (name == 'venv' or re.fullmatch(r'releases/release-[A-Za-z0-9_-]+', name)):
        raise ValueError('Invalid installed version.')
    path = prefix / name
    if path.is_symlink() or path.resolve() != path or not (path / 'pyvenv.cfg').is_file() or not (path / 'bin/python').is_file():
        raise ValueError('Installed version is missing or has moved.')
    return path


def read_installation(prefix):
    path = prefix / 'installation.json'
    if not path.exists() and not path.is_symlink(): return None
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('Invalid installation record.')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('format') != 1:
        raise ValueError('Unsupported installation record.')
    release_path(prefix, data.get('active'))
    launcher = data.get('launcher')
    if not isinstance(launcher, str) or not Path(launcher).is_absolute():
        raise ValueError('Invalid launcher record.')
    runner = prefix / 'desktop-buddy'
    if runner.is_symlink() or not runner.is_file() or hashlib.sha256(runner.read_bytes()).hexdigest() != data.get('runner_sha256'):
        raise ValueError('The installed launcher has changed. Existing files were left in place.')
    return data


def verify_environment(environment, *, complete=True):
    code = 'import desktop_buddy.ui, desktop_buddy.__main__'
    if complete:
        code += '; import desktop_buddy.mcp_client, desktop_buddy.attachments; from mcp import Client; import jsonschema'
    subprocess.run([str(environment / 'bin/python'), '-I', '-c', code], check=True, cwd='/tmp', timeout=30)


def build_environment(environment, *, download=False, wheelhouse=None):
    import gi
    for name, version in [('Gtk', '4.0'), ('Secret', '1'), ('Atspi', '2.0'), ('Gst', '1.0')]:
        gi.require_version(name, version)
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='buddy-wheel-') as temporary:
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                        '--no-index', '--wheel-dir', temporary, str(repository)], check=True)
        wheel = next(Path(temporary).glob('desktop_buddy-*.whl'))
        # Create at the final path: moving a venv breaks its absolute shebangs.
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(environment)
        options = [] if download else ['--no-index']
        if wheelhouse: options += ['--find-links', str(wheelhouse)]
        subprocess.run([sys.executable, '-m', 'pip', '--python', str(environment / 'bin/python'),
                        'install', *options, str(wheel)], check=True)
    verify_environment(environment)


def install(prefix, desktop, builder=build_environment, *, upgrade=False):
    prefix = Path(prefix).expanduser().resolve()
    with installation_lock(prefix):
        old = read_installation(prefix)
        runner = prefix / 'desktop-buddy'
        record = prefix / 'installation.json'
        if old and not upgrade:
            raise ValueError('Buddy is already installed. Use --upgrade to update it.')
        if desktop is None:
            if not old: raise ValueError('A launcher location is required for the first installation.')
            desktop = Path(old['launcher'])
        desktop = Path(desktop).absolute()
        expected = desktop_text(runner)
        if old and str(desktop) != old['launcher']:
            raise ValueError('Use the existing launcher location when updating.')
        legacy = not old and upgrade
        if legacy:
            release_path(prefix, 'venv')
            if desktop.is_symlink() or not desktop.is_file() or desktop.read_text() != desktop_text(prefix / 'venv/bin/desktop-buddy', legacy=True):
                raise ValueError('The existing launcher is not a recognized Buddy installation.')
        elif old:
            # An interrupted first install can have a valid release but no desktop
            # entry. An interrupted legacy migration can still have its old entry.
            allowed = {expected}
            if 'venv' in (old.get('active'), old.get('previous')):
                allowed.add(desktop_text(prefix / 'venv/bin/desktop-buddy', legacy=True))
            if desktop.is_symlink() or (desktop.exists() and (not desktop.is_file() or desktop.read_text() not in allowed)):
                raise ValueError('The desktop launcher has changed. Existing files were left in place.')
        elif desktop.exists() or desktop.is_symlink() or (prefix / 'venv').exists():
            raise ValueError('An installation or launcher already exists.')
        runner_exists = runner.exists()
        if not old and (runner.is_symlink() or (runner_exists and (not runner.is_file() or runner.read_text() != RUNNER))):
            raise ValueError('An existing launcher was found in the installation folder.')
        releases = prefix / 'releases'
        if releases.is_symlink(): raise ValueError('The releases folder cannot be a link.')
        releases.mkdir(exist_ok=True)
        environment = Path(tempfile.mkdtemp(prefix='release-', dir=releases))
        desktop_exists = desktop.exists()
        needs_desktop = not desktop_exists or desktop.read_text() != expected
        try:
            builder(environment)
            active = str(environment.relative_to(prefix))
            release_path(prefix, active)
            info = {'format': 1, 'active': active,
                    'previous': old['active'] if old else 'venv' if legacy else None,
                    'launcher': str(desktop), 'runner_sha256': hashlib.sha256(RUNNER.encode()).hexdigest()}
            if old:
                # Keep the existing stable wrapper; future wrapper migrations must
                # explicitly support both the old and the new manifest formats.
                info['runner_sha256'] = old['runner_sha256']
            elif not runner_exists:
                publish(runner, RUNNER, exclusive=True, mode=0o755)
            # Select valid code before advertising the wrapper. During legacy
            # migration the old desktop entry continues to open its retained venv.
            publish(record, json.dumps(info, indent=2) + '\n', exclusive=not old)
            if needs_desktop:
                publish(desktop, expected, exclusive=not desktop_exists, mode=0o644)
            return info
        except BaseException:
            # Inspect the committed record even if an injected/OS error occurs
            # after publication. Never remove code referenced by the launcher.
            try: selected = json.loads(record.read_text()).get('active') == str(environment.relative_to(prefix))
            except (OSError, ValueError, AttributeError): selected = False
            if not selected:
                if not old and not runner_exists and runner.is_file() and runner.read_text() == RUNNER:
                    runner.unlink()
                shutil.rmtree(environment)
            raise


def rollback(prefix, verify=None):
    prefix = Path(prefix).expanduser().resolve()
    with installation_lock(prefix):
        info = read_installation(prefix)
        if not info or not info.get('previous'):
            raise ValueError('There is no previous version to restore.')
        previous = release_path(prefix, info['previous'])
        if verify is None: verify_environment(previous, complete=False)
        else: verify(previous)
        info['active'], info['previous'] = info['previous'], info['active']
        publish(prefix / 'installation.json', json.dumps(info, indent=2) + '\n')
        return info


def main():
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not data_home.is_absolute(): data_home = Path.home() / '.local/share'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=data_home / 'desktop-buddy')
    parser.add_argument('--desktop-dir', type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--upgrade', action='store_true')
    action.add_argument('--rollback', action='store_true')
    parser.add_argument('--download-dependencies', action='store_true')
    parser.add_argument('--with-mcp', action='store_true', help='Compatibility alias for --download-dependencies; MCP is always included.')
    parser.add_argument('--wheelhouse', type=Path, help='Folder of dependency wheels for offline installation.')
    args = parser.parse_args()
    if os.geteuid() == 0: parser.error('Run as your ordinary desktop user, without sudo.')
    prefix = args.prefix.expanduser().resolve()
    desktop = args.desktop_dir.expanduser().resolve() / 'io.desktopbuddy.App.desktop' if args.desktop_dir else None
    if desktop is None and not (prefix / 'installation.json').exists():
        desktop = data_home / 'applications/io.desktopbuddy.App.desktop'
    try:
        if args.rollback: info = rollback(prefix)
        else:
            info = install(prefix, desktop, lambda path: build_environment(path,
                download=args.download_dependencies or args.with_mcp, wheelhouse=args.wheelhouse), upgrade=args.upgrade)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Installation did not finish: {error}', file=sys.stderr)
        return 1
    print('Desktop launcher: ' + info['launcher'])
    print('Run: ' + str(prefix / 'desktop-buddy'))
    print('The selected version will open next time you start Buddy. Conversations and saved sign-ins were not moved or changed.')
    return 0


if __name__ == '__main__': sys.exit(main())
