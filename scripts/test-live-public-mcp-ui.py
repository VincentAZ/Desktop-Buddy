#!/usr/bin/env python3
"""Opt-in real public MCP connection and use after a separate-process restart.

Only Microsoft Learn public documentation is queried. No account credentials,
model calls, browser automation or personal profile changes are involved.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--live',action='store_true')
parser.add_argument('--stage',choices=['connect','reopen'])
parser.add_argument('--root',type=Path)
args=parser.parse_args()
if not args.live:raise SystemExit('Use --live to contact the public Microsoft Learn MCP service.')
if os.environ.get('GDK_BACKEND')!='x11' or os.environ.get('WAYLAND_DISPLAY'):
    raise SystemExit('Run on a private X display with GDK_BACKEND=x11 and WAYLAND_DISPLAY unset.')
if not args.stage:
    parent=ROOT/'.test-data'/'live-public-mcp';parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='run-',dir=parent))
    print('Evidence: '+str(root/'result.json'),flush=True)
    result={'scope':'Actual public MCP through native Connect; two separate app processes and an isolated profile. No account authorization tested.'}
    try:
        for stage in ('connect','reopen'):
            with (root/(stage+'.log')).open('w') as log:
                subprocess.run([sys.executable,__file__,'--live','--stage',stage,'--root',str(root)],
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=100)
            result[stage]=json.loads((root/(stage+'.json')).read_text())
            print(stage+': passed',flush=True)
        assert result['connect']['connection_id']==result['reopen']['connection_id']
        result['status']='passed'
    except Exception as error:result.update(status='failed',error=repr(error))
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['status']=='passed' else 1)

import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from desktop_buddy.ui import BuddyApp
from desktop_buddy.mcp_client import MCPManager
from desktop_buddy.security import Policy,StopToken
from desktop_buddy.tools import Registry

class NoCredentials:
    def __init__(self,redactor):self.redactor=redactor
    def get(self,_):return ''
    def set(self,*_):raise AssertionError('A public connection must not store account credentials')

root=args.root;app=BuddyApp(root/'profile');app.mcp.close()
app.credentials=NoCredentials(app.redactor);app.mcp=MCPManager(app.store,app.credentials)
app.store.set('local_only',False)
work=root/'work';work.mkdir(exist_ok=True)
result={};phase=0;started=time.monotonic();done=threading.Event();token=StopToken();worker=None

def use_connection(ident):
    try:
        registry=Registry(Policy(work,'execute',False,lambda name,*_:name in ('mcp.tools','mcp.call')),app.store,mcp=app.mcp)
        definitions=registry.execute('mcp.tools',{'server':ident},token)['tools']
        (root/(args.stage+'-tools.json')).write_text(json.dumps(definitions,indent=2)+'\n')
        tool=next((t for t in definitions if t['name']=='microsoft_docs_search'),None)
        assert tool is not None, 'The expected public documentation search is unavailable'
        answer=registry.execute('mcp.call',{'server':ident,'name':tool['name'],
            'arguments':{'query':'Microsoft Learn MCP server authentication requirements'}},token)
        (root/(args.stage+'-search.json')).write_text(json.dumps(answer,indent=2)+'\n')
        assert not answer.get('isError'), 'The service reported a tool error'
        assert 'https://learn.microsoft.com/' in json.dumps(answer), 'No documentation links returned'
        assert app.mcp.servers()[0]['status']=='connected'
        result.update(status='passed',connection_id=ident,public_search_returned_documentation=True,
                      tools=[t['name'] for t in definitions])
    except Exception as error:result.update(status='failed',error=repr(error),traceback=traceback.format_exc())
    finally:done.set()

def tick():
    global phase,dialog,worker
    try:
        if time.monotonic()-started>80:raise TimeoutError('Public MCP phase '+str(phase))
        if phase==0:
            dialog=app.mcp_dialog().controller
            if args.stage=='connect':
                assert not app.mcp.configs()
                dialog.search.set_text('Microsoft Learn');dialog.fill_catalog()
                assert dialog.catalog_buttons['microsoft-learn'].get_sensitive()
                assert not dialog.advanced.get_expanded()
                dialog.catalog_buttons['microsoft-learn'].emit('clicked');phase=1
            else:
                saved=app.mcp.servers();assert len(saved)==1
                assert saved[0]['status']=='saved · reconnects on use'
                assert not app.mcp.connections
                result['saved_connection_loaded_without_sign_in']=True
                worker=threading.Thread(target=use_connection,args=(saved[0]['id'],),daemon=True);worker.start();phase=2
        elif phase==1 and not dialog.busy:
            assert dialog.status.get_text().startswith('Connected to'),dialog.status.get_text()
            saved=app.mcp.servers();assert len(saved)==1
            result['native_connect_completed_without_technical_setup']=True
            worker=threading.Thread(target=use_connection,args=(saved[0]['id'],),daemon=True);worker.start();phase=2
        elif phase==2 and done.is_set():
            app.quit();return False
    except Exception as error:
        result.update(status='failed',error=repr(error),traceback=traceback.format_exc());token.stop();app.quit();return False
    return True

GLib.timeout_add(100,tick)
try:app.run([])
finally:
    token.stop()
    if worker:worker.join(5)
    app.mcp.close();app.store.close()
    (root/(args.stage+'.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
raise SystemExit(0 if result.get('status')=='passed' else 1)
