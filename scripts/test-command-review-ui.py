#!/usr/bin/env python3
"""Plain-language command review retains exact details and real allow/deny behavior."""
from pathlib import Path
import json,sys,tempfile,time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp,content

root=Path(__file__).resolve().parents[1]/'.test-data'/'command-review';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root);app=BuddyApp(Path(profile.name)/'state')
work=Path(profile.name)/'work';work.mkdir();app.store.set('workspace',str(work))
config=app.store.configs()['ollama'];config.model='fixture';app.store.save_config(config)
app.store.set('provider','ollama');app.store.set('local_only',True)
app.credentials.get=lambda _:''
phase=0;result={};started=time.monotonic();attempt=0

class FixtureProvider:
    def __init__(self,config,*_):
        global attempt
        attempt+=1;self.attempt=attempt;self.config=config;self.round=0
    def check(self,*_):pass
    def complete(self,messages,*_):
        self.round+=1
        if self.round==1:
            target='summary.txt' if self.attempt==1 else 'declined.txt'
            code=f'from pathlib import Path; Path({target!r}).write_text("Quarterly summary fixture")'
            return {'role':'assistant','content':'','tool_calls':[{'id':'write','name':'run_tool','arguments':{
                'name':'shell.run','arguments':{'argv':['/usr/bin/python3','-c',code],'timeout':30},
                'purpose':'Save the quarterly summary as '+target+' in Buddy’s work folder.'}}]},{}
        message=next(m for m in reversed(messages) if m['role']=='tool')
        assert bool(message['error'])==(self.attempt==2)
        return {'role':'assistant','content':'Summary saved.' if self.attempt==1 else 'That step was skipped.'},{}

def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from walk(child);child=child.get_next_sibling()

def click(window,title):next(w for w in walk(window) if isinstance(w,Gtk.Button) and w.get_label()==title).emit('clicked')

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()));node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name));return True

def tick():
    global phase,dialog,details
    try:
        if time.monotonic()-started>25:raise TimeoutError(f'phase {phase}')
        if phase==0:app.send('Save a quarterly summary in my work folder.');phase=1
        elif phase==1 and app.pending:
            dialog=app.pending[0][1]
            if not capture(dialog,'plain-review.png'):return True
            shown='\n'.join(w.get_text() for w in walk(dialog) if isinstance(w,Gtk.Label) and w.get_mapped())
            assert 'Save the quarterly summary as summary.txt' in shown
            assert 'Internet access is blocked' in shown and '30 seconds' in shown
            assert '/usr/bin/python3' not in shown and 'write_text' not in shown
            details=next(w for w in walk(dialog) if isinstance(w,Gtk.Expander) and w.get_label()=='Technical details')
            assert not details.get_expanded();details.set_expanded(True);phase=2
        elif phase==2:
            raw=next(w for w in walk(details) if isinstance(w,Gtk.TextView))
            assert raw.get_mapped()
            arguments=json.loads(content(raw))
            assert arguments['argv'][0]=='/usr/bin/python3' and 'summary.txt' in arguments['argv'][2]
            details.set_expanded(False);click(dialog,'Allow once');phase=3
        elif phase==3 and app.worker and not app.worker.is_alive():
            assert (work/'summary.txt').read_text()=='Quarterly summary fixture'
            assert app.agent.review_context is None
            result['plain_proposal_exact_details_and_approved_effect']=True
            app.send('Save another copy.');phase=4
        elif phase==4 and app.pending:
            click(app.pending[0][1],'Skip action');phase=5
        elif phase==5 and not app.worker.is_alive():
            assert not (work/'declined.txt').exists()
            assert app.agent.review_context is None
            result['skip_prevents_effect_and_clears_review_context']=True
            app.quit();return False
    except Exception as error:
        result['error']=repr(error);app.emergency('test failure');app.quit();return False
    return True

GLib.timeout_add(150,tick)
try:
    with patch('desktop_buddy.ui.Provider',FixtureProvider):app.run([])
finally:
    if app.worker:app.worker.join(5)
    app.mcp.close();app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
