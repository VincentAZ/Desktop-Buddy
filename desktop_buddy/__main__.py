from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def doctor():
    from .diagnostics import collect
    info=collect()
    info['own_app_scope']=in_own_scope()
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
    unit=f'app-gnome-{APP_ID}-{os.getpid()}'
    flags=['--user','--scope','--quiet','--collect','--slice','app.slice']
    try:
        probe=subprocess.run([runner,*flags,'--unit',unit+'-probe','true'],stdin=subprocess.DEVNULL,capture_output=True,timeout=5)
    except (OSError,subprocess.SubprocessError):return
    if probe.returncode!=0:return
    os.environ['DESKTOP_BUDDY_SCOPED']='1'
    try:os.execvp(runner,[runner,*flags,'--unit',unit,sys.executable,'-m','desktop_buddy',*argv])
    except OSError:return  # start unscoped rather than not at all


def main():
    parser=argparse.ArgumentParser(description='Desktop Buddy — standalone Linux desktop assistant')
    from .paths import default_data_dir
    parser.add_argument('--data-dir',default=str(default_data_dir()))
    parser.add_argument('--doctor',action='store_true');parser.add_argument('--smoke-test',action='store_true')
    args=parser.parse_args()
    if os.geteuid()==0:parser.error('Desktop Buddy refuses to run as root. Use your ordinary desktop account.')
    if args.doctor:doctor();return
    enter_own_scope(sys.argv[1:])
    from .ui import BuddyApp,Gdk
    if Gdk.Display.get_default() is None:
        parser.exit(2,'Desktop Buddy cannot access a desktop display. Launch from a terminal in your unlocked GNOME session. Use --doctor for read-only diagnostics.\n')
    app=BuddyApp(args.data_dir,args.smoke_test)
    code=app.run([])
    raise SystemExit(1 if getattr(app,'startup_error',None) else code)

if __name__=='__main__':main()
