#!/usr/bin/env python3
"""Opt-in live local/cloud switching through GTK; only synthetic work files.

Requires existing LM Studio and Anthropic configurations and a saved cloud key.
Reads configuration and credentials without changing the personal profile.
Run with --live on a private X display. This is not part of the automatic suite.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
if '--live' not in sys.argv:raise SystemExit('Use --live to authorize real configured provider requests with synthetic data.')
if os.environ.get('GDK_BACKEND')!='x11' or os.environ.get('WAYLAND_DISPLAY'):
    raise SystemExit('Run on a private X display with GDK_BACKEND=x11 and WAYLAND_DISPLAY unset.')
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from desktop_buddy.ui import BuddyApp,content
from desktop_buddy.paths import default_data_dir
from desktop_buddy.state import ProviderConfig,PROVIDERS
from desktop_buddy.tools import Registry
from desktop_buddy.security import Denied

connection=sqlite3.connect((default_data_dir()/'state.sqlite3').as_uri()+'?mode=ro',uri=True)
try:
    saved=json.loads(connection.execute("SELECT value FROM settings WHERE key='providers'").fetchone()[0])
finally:connection.close()
configs={pid:ProviderConfig(**saved[pid]) for pid in ('lmstudio','anthropic')}
for config in configs.values():
    if not config.model:raise SystemExit('Both existing providers need a selected model.')
    config.tools=True;config.vision=False;config.reasoning='';config.max_output=1024;config.validate()

parent=ROOT/'.test-data'/'live-model-switch';parent.mkdir(parents=True,exist_ok=True)
root=Path(tempfile.mkdtemp(prefix='run-',dir=parent));work=root/'work';work.mkdir()
app=BuddyApp(root/'profile');app.store.set('workspace',str(work));app.store.set('local_only',False)
app.store.set('provider','lmstudio');app.store.set('mode','execute')
for config in configs.values():app.store.save_config(config)
get_key=app.credentials.get
app.credentials.get=lambda pid:'' if pid=='lmstudio' else get_key(pid) if pid=='anthropic' else ''
app.credentials.set=lambda *_:(_ for _ in ()).throw(AssertionError('This check never changes saved credentials'))
if not app.credentials.get('anthropic'):raise SystemExit('No saved Anthropic credential; live check cannot proceed.')
app.approve=lambda name,*_:name=='Cloud data disclosure'
report={'scope':'Real UI switching with configured LM Studio and Anthropic, synthetic conversation/files only. No personal profile writes or external app actions.',
        'models':{pid:c.model for pid,c in configs.items()},'tasks':[]}
print('Evidence: '+str(root/'result.json'),flush=True)
allowed={work/name for name in ('local.txt','cloud.txt','return.txt')}

def scoped_registry(*args,**kwargs):
    registry=Registry(*args,**kwargs)
    assert registry.policy.workspace.resolve()==work, 'Only the isolated test workspace is authorized'
    registry.tools={name:tool for name,tool in registry.tools.items() if name in
                    {'fs.read','fs.write','task.checkpoint','buddy.help','buddy.status'}}
    execute=registry.execute
    def bounded(name,arguments,*rest):
        if name.startswith('fs.') and (work/arguments['path']).resolve() not in allowed:
            raise Denied('Only the three synthetic test files are in scope.')
        return execute(name,arguments,*rest)
    registry.execute=bounded
    return registry

phase=0;started=time.monotonic();cid=None;code='BUD-'+root.name.removeprefix('run-')

def choose(pid):
    dialog=app.model_dialog();picker=app.model_menu;row=picker.box.get_first_child()
    while row:
        if isinstance(row,Gtk.CheckButton):
            title=row.get_child().get_first_child();hint=title.get_next_sibling()
            if title.get_text()==configs[pid].model and hint.get_text().startswith(PROVIDERS[pid][0]+' ·'):
                row.set_active(True);picker.popover.popdown();dialog.controller.close()
                assert app.selected_id()==pid
                return
        row=row.get_next_sibling()
    raise AssertionError('Saved assistant was missing from the IT selector')

def send(text):
    app.input.get_buffer().set_text(text);app.run_button.emit('clicked')
    assert app.worker is not None

def finished():return app.worker is not None and not app.worker.is_alive()

def check(pid,name,expected):
    assert app.cid==cid and app.agent.provider.config.id==pid
    task=app.store.tasks(cid)[0];report['tasks'].append({'provider':pid,'status':task['status']})
    assert task['status']=='complete',task['status']
    assert (work/name).read_text().strip()==expected, 'File content did not match'
    assert any(m['role']=='tool' for m in app.store.messages(cid)), 'No actual tool calls'

def tick():
    global phase,cid
    try:
        if time.monotonic()-started>150:raise TimeoutError('Live model switch phase '+str(phase))
        if phase==0:
            cid=app.cid;choose('lmstudio')
            send('This synthetic test uses conversation code '+code+'. Keep that code in this conversation only. Create local.txt in the work folder with exactly LOCAL_OK, then read it back. Use the file tools; no other files or apps are in scope.');phase=1
        elif phase==1 and finished():
            check('lmstudio','local.txt','LOCAL_OK')
            app.input.get_buffer().set_text('Preserve this draft');choose('anthropic')
            assert content(app.input)=='Preserve this draft' and app.cid==cid
            report['switch_preserved_draft_and_conversation']=True
            send('Use the conversation code from my first message as the exact contents of cloud.txt in the work folder, then read it back. Use the file tools; no other files or apps are in scope.');phase=2
        elif phase==2 and finished():
            check('anthropic','cloud.txt',code);choose('lmstudio')
            send('Use that same conversation code as the exact contents of return.txt in the work folder, then read it back. Use the file tools; no other files or apps are in scope.');phase=3
        elif phase==3 and finished():
            check('lmstudio','return.txt',code)
            report.update(status='passed',same_conversation_across_three_tasks=True,keyless_local_model=True)
            app.quit();return False
    except Exception as error:
        report.update(status='failed',error=repr(error),traceback=traceback.format_exc(),phase=phase)
        app.cancel();app.quit();return False
    return True

GLib.timeout_add(100,tick)
try:
    with patch('desktop_buddy.ui.Registry',scoped_registry):app.run([])
finally:
    app.start_token.stop()
    if app.agent:app.agent.stop()
    if app.worker:app.worker.join(5)
    if cid:(root/'messages.json').write_text(json.dumps(app.store.messages(cid),indent=2)+'\n')
    report['seconds']=round(time.monotonic()-started,2)
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    app.browser.close();app.mcp.close();app.store.close()
print(json.dumps(report,indent=2))
raise SystemExit(0 if report.get('status')=='passed' else 1)
