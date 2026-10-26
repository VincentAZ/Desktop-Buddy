from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import stat
import subprocess
import sys


def local_port(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.25):
            return True
    except OSError:
        return False


def gpu_info():
    executable=shutil.which('nvidia-smi')
    if not executable:return []
    try:
        result=subprocess.run([executable,'--query-gpu=name,memory.total,driver_version','--format=csv,noheader,nounits'],
            stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=3,check=True)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError,subprocess.SubprocessError):
        return []

def doctor():
    import importlib.util
    info={'os':platform.platform(),'python':platform.python_version(),'desktop':os.environ.get('XDG_CURRENT_DESKTOP'),
        'session':os.environ.get('XDG_SESSION_TYPE'),'display':os.environ.get('WAYLAND_DISPLAY') or os.environ.get('DISPLAY'),
        'own_app_scope':in_own_scope(),
        'dependencies':{n:bool(importlib.util.find_spec(n)) for n in ['requests','gi','PIL']},
        'commands':{n:shutil.which(n) for n in ['bwrap','google-chrome','chromium','ollama','lms','docker','cursor','ptyxis','nvidia-smi','xdotool','wmctrl']},
        'local_services':{'lm_studio_1234':local_port(1234),'ollama_11434':local_port(11434),'comfyui_8188':local_port(8188)},
        'gpus':gpu_info()}
    docker_socket=Path('/var/run/docker.sock')
    if docker_socket.exists():
        mode=docker_socket.stat().st_mode
        info['docker_socket']={'path':str(docker_socket),'mode':stat.filemode(mode),
            'read_write_access':os.access(docker_socket,os.R_OK|os.W_OK)}
    try:
        import gi
        from gi.repository import Gio
        bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        result=bus.call_sync('org.freedesktop.portal.Desktop','/org/freedesktop/portal/desktop','org.freedesktop.DBus.Properties','GetAll',
            __import__('gi.repository',fromlist=['GLib']).GLib.Variant('(s)',('org.freedesktop.portal.RemoteDesktop',)),None,Gio.DBusCallFlags.NONE,3000,None)
        info['remote_desktop_portal']=result.unpack()[0]
    except Exception as e:info['portal_error']=str(e)
    print(json.dumps(info,indent=2))

APP_ID='io.desktopbuddy.App'


def in_own_scope():
    try:cgroup=Path('/proc/self/cgroup').read_text()
    except OSError:return True
    # A scope belonging to another app (a terminal, an IDE) would make GNOME file
    # our permissions under that app's name, so only our own scope counts.
    return f'-{APP_ID}-' in cgroup and cgroup.rstrip().endswith('.scope')


def enter_own_scope(argv):
    """GNOME's portals identify a desktop app by its systemd scope. Launched from a
    desktop icon or a terminal, the process lands in the launcher's cgroup, so the
    portal sees no app and cannot remember permissions ("Remember This Selection"
    silently does nothing). Re-run inside a scope named for this app."""
    if os.environ.get('DESKTOP_BUDDY_SCOPED') or in_own_scope():return
    runner=shutil.which('systemd-run')
    if not runner:return
    try:
        probe=subprocess.run([runner,'--user','--scope','--quiet','--collect','true'],stdin=subprocess.DEVNULL,
                             capture_output=True,timeout=5)
    except (OSError,subprocess.SubprocessError):return
    if probe.returncode!=0:return
    os.environ['DESKTOP_BUDDY_SCOPED']='1'
    unit=f'app-gnome-{APP_ID}-{os.getpid()}'
    os.execvp(runner,[runner,'--user','--scope','--quiet','--collect','--unit',unit,'--slice','app.slice',
                      sys.executable,'-m','desktop_buddy',*argv])


def main():
    parser=argparse.ArgumentParser(description='Desktop Buddy — standalone Linux desktop assistant')
    from .paths import default_data_dir
    parser.add_argument('--data-dir',default=str(default_data_dir()))
    parser.add_argument('--doctor',action='store_true');parser.add_argument('--smoke-test',action='store_true')
    args=parser.parse_args()
    if os.geteuid()==0:parser.error('Desktop Buddy refuses to run as root. Use your ordinary desktop account.')
    if args.doctor:doctor();return
    enter_own_scope(sys.argv[1:])
    from .ui import BuddyApp
    app=BuddyApp(args.data_dir,args.smoke_test)
    raise SystemExit(app.run([]))

if __name__=='__main__':main()
