"""Read-only host probes. An inaccessible service is not an absent service."""
from __future__ import annotations

import errno
import importlib.util
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess


def socket_probe(address, family=socket.AF_INET):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(.3)
            connection.connect(address)
        return {'status': 'reachable'}
    except OSError as error:
        if error.errno in (errno.EPERM, errno.EACCES):
            status = 'blocked'
        elif error.errno in (errno.ECONNREFUSED, errno.ENOENT):
            status = 'unavailable'
        else:
            status = 'unknown'
        return {'status': status, 'reason': str(error)}


def command_probe(argv):
    """Fixed diagnostic commands only; no shell or ambient credential output."""
    if not shutil.which(argv[0]):
        return {'status': 'unavailable', 'reason': 'Command is not installed.'}
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return {'status': 'unknown', 'reason': str(error)}
    if result.returncode:
        return {'status': 'unknown', 'returncode': result.returncode,
                'reason': (result.stderr or result.stdout).strip()[:1000]}
    return {'status': 'available', 'output': result.stdout.strip()[:2000]}


def desktop_probe():
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync('org.freedesktop.portal.Desktop',
            '/org/freedesktop/portal/desktop', 'org.freedesktop.DBus.Properties',
            'GetAll', GLib.Variant('(s)', ('org.freedesktop.portal.RemoteDesktop',)),
            None, Gio.DBusCallFlags.NONE, 3000, None)
        return {'status': 'available', 'properties': result.unpack()[0],
                'consent': 'Not requested; screen/input access is not proven by this probe.'}
    except Exception as error:
        return {'status': 'unknown', 'reason': str(error),
                'next': 'Run from a terminal in the unlocked GNOME session. No permission changes are needed.'}


def screen_activity_probe(bus=None):
    """Read screen-saver activity. This does not establish whether the session is password-locked."""
    try:
        import gi
        gi.require_version('Gio','2.0')
        from gi.repository import Gio
        if bus is None:bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        locked=bus.call_sync('org.gnome.ScreenSaver','/org/gnome/ScreenSaver',
            'org.gnome.ScreenSaver','GetActive',None,None,Gio.DBusCallFlags.NONE,2000,None).unpack()[0]
        return {'status':'active' if locked else 'inactive','source':'org.gnome.ScreenSaver.GetActive'}
    except Exception as error:return {'status':'unknown','reason':str(error)}


def collect():
    commands = ['bwrap', 'google-chrome', 'chromium', 'ollama', 'lms', 'docker',
                'cursor', 'ptyxis', 'nvidia-smi', 'xdotool', 'wmctrl']
    services = {name: socket_probe(('127.0.0.1', port)) for name, port in
                [('lm_studio_1234', 1234), ('ollama_11434', 11434), ('comfyui_8188', 8188)]}
    gpu = command_probe(['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
                         '--format=csv,noheader,nounits'])
    docker = socket_probe('/var/run/docker.sock', socket.AF_UNIX)
    portal = desktop_probe()
    screen_activity = screen_activity_probe()
    try:
        distro = platform.freedesktop_os_release().get('PRETTY_NAME', '')
    except OSError:
        distro = ''
    apps = []
    app_error = ''
    try:
        import gi
        from gi.repository import Gio
        apps = [{'id': app.get_id(), 'name': app.get_display_name()}
                for app in Gio.AppInfo.get_all() if app.should_show() and
                any(word in (app.get_id() + ' ' + app.get_display_name()).casefold()
                    for word in ('lm studio', 'lmstudio', 'ollama', 'comfy', 'cursor', 'chrome', 'hermes', 'ptyxis'))]
    except Exception as error:
        app_error = str(error)
    warnings = []
    if screen_activity['status']!='inactive':
        warnings.append('An inactive GNOME screen saver is not confirmed; physical focus checks must wait. This does not prove that the session is locked.')
    if any(probe['status'] == 'blocked' for probe in services.values()):
        warnings.append('Loopback probing is blocked in this process; local services may be running on the host.')
    if portal['status'] != 'available':
        warnings.append('Physical desktop access is unverified. Run host-side checks from the GNOME session.')
    if gpu['status'] != 'available':
        warnings.append('GPU inventory is unavailable here; this does not establish a missing GPU or broken host driver.')
    return {
        'os': platform.platform(), 'distribution': distro, 'python': platform.python_version(),
        'desktop': os.environ.get('XDG_CURRENT_DESKTOP'),
        'session': os.environ.get('XDG_SESSION_TYPE'),
        'display': os.environ.get('WAYLAND_DISPLAY') or os.environ.get('DISPLAY'),
        'dependencies': {name: importlib.util.find_spec(name) is not None
                         for name in ('requests', 'gi', 'PIL', 'openpyxl', 'docx', 'pypdf', 'mcp', 'jsonschema')},
        'commands': {name: shutil.which(name) for name in commands},
        'service_probes': services,
        'local_services': {name: value['status'] == 'reachable' for name, value in services.items()},
        'gpu_probe': gpu, 'gpus': gpu.get('output', '').splitlines(),
        'docker_socket': {'path': '/var/run/docker.sock', **docker},
        'desktop_portal': portal, 'screen_saver':screen_activity, 'applications': apps,
        'application_probe_error': app_error, 'warnings': warnings,
    }
