#!/usr/bin/env python3
"""Audit rendered app surfaces, including OPEN native dropdowns, in every theme.

Uses a temporary profile, synthetic content, and no external requests or keys.
Run under GTK_THEME=Yaru-dark and Adwaita to cover mismatched desktop themes.
"""
from collections import Counter
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, GLib, Graphene
from PIL import Image
from desktop_buddy.ui import BuddyApp
from desktop_buddy.themes import THEMES
from desktop_buddy.desktop_access_ui import ComputerAccessDialog
from desktop_buddy.mcp_client import MCPConfig
from desktop_buddy.security import StopToken, Cancelled

root = Path(__file__).resolve().parents[1]/'.test-data'/'theme-surfaces'/os.environ.get('GTK_THEME', 'desktop')
root.mkdir(parents=True, exist_ok=True)
profile = tempfile.TemporaryDirectory(dir=root)
app = BuddyApp(Path(profile.name)/'state')
app.credentials.get = lambda *_: ''
app.store.set('window_width', 1180)
app.store.set('window_height', 860)
config = app.store.configs()['openai']
config.model = 'fixture-model'
config.reasoning = 'high'
app.store.save_config(config)
app.store.set('provider', 'openai')
app.store.set('local_only', False)
app.store.save_memory({'Reports': 'Include a summary and totals.'})
app.store.audit('', 'fixture', {'verified':True, 'text':'Synthetic activity'})
result = {'surfaces':{}, 'failures':[]}


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def luminance(color):
    values = [c/255 for c in color]
    linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in values]
    return sum(c*w for c,w in zip(linear, (.2126,.7152,.0722)))


def contrast(fg, bg):
    light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (light+.05)/(dark+.05)


def screenshot(widget, name):
    snapshot = Gtk.Snapshot.new()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, float(widget.get_width()), float(widget.get_height()))
    node = snapshot.to_node()
    if node is None:return None  # Wait for GTK's first frame after opening a surface.
    rect = Graphene.Rect()
    rect.init(0, 0, widget.get_width(), widget.get_height())
    path = root/(app.theme+'-'+name+'.png')
    widget.get_renderer().render_texture(node, rect).save_to_png(str(path))
    return Image.open(path).convert('RGB')


def audit(widget, name):
    image = screenshot(widget, name)
    if image is None:return False
    checks = []
    for label in walk(widget):
        if not isinstance(label, (Gtk.Label,Gtk.Text,Gtk.TextView)) or not label.get_mapped():continue
        if isinstance(label,Gtk.TextView):
            buffer=label.get_buffer()
            text=buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
        else:text=label.get_text()
        if not text.strip():continue
        # Popovers have their own native surface and are captured separately.
        ancestor = label.get_parent()
        nested = False
        while ancestor and ancestor is not widget:
            if isinstance(ancestor, Gtk.Popover):nested = True;break
            ancestor = ancestor.get_parent()
        if nested:continue
        ok, bounds = label.compute_bounds(widget)
        if not ok:continue
        x0, y0 = bounds.get_x(), bounds.get_y()
        x1, y1 = x0+bounds.get_width(), y0+bounds.get_height()
        ancestor = label.get_parent()
        while ancestor and ancestor is not widget:
            if isinstance(ancestor, Gtk.ScrolledWindow):
                ok, clip = ancestor.compute_bounds(widget)
                if ok:
                    x0, y0 = max(x0,clip.get_x()), max(y0,clip.get_y())
                    x1, y1 = min(x1,clip.get_x()+clip.get_width()), min(y1,clip.get_y()+clip.get_height())
            ancestor = ancestor.get_parent()
        x0,y0,x1,y1 = map(round,(max(0,x0),max(0,y0),min(image.width,x1),min(image.height,y1)))
        if x1-x0 < 5 or y1-y0 < 8:continue
        crop = image.crop((x0,y0,x1,y1))
        colors = Counter({color:count for count,color in crop.getcolors(crop.width*crop.height)})
        bg = colors.most_common(1)[0][0]
        color = label.get_style_context().get_color()
        fg = tuple(round(v*255) for v in (color.red,color.green,color.blue))
        # Compare actual rendered ink as well as declared CSS; catches native opacity.
        direction = tuple(f-b for f,b in zip(fg,bg))
        ink = max(colors, key=lambda c:sum((v-b)*d for v,b,d in zip(c,bg,direction)))
        measured = min(contrast(fg,bg), contrast(ink,bg))
        checks.append(measured)
        # Disabled controls are still audited and recorded, but exempt from AA.
        if measured < 4.5 and label.is_sensitive():
            result['failures'].append({'theme':app.theme, 'surface':name, 'text':text[:90],
                                       'ratio':round(measured,2), 'fg':fg, 'bg':bg})
    assert checks, name
    result['surfaces'][app.theme+'/'+name] = {'labels':len(checks), 'min_contrast':round(min(checks),2)}
    return True


def screen(widget, name, scroll=False):
    yield
    while not audit(widget, name):yield
    if scroll:
        scrolls = [w for w in walk(widget) if isinstance(w, Gtk.ScrolledWindow) and w.get_mapped()]
        for fraction in (.5, 1):
            for scroller in scrolls:
                adj = scroller.get_vadjustment()
                adj.set_value((adj.get_upper()-adj.get_page_size())*fraction)
            yield
            while not audit(widget, name+'-scroll-'+str(fraction)):yield
        for scroller in scrolls:scroller.get_vadjustment().set_value(0)
        yield


def dropdown(widget, name):
    widget.grab_focus()
    yield
    toggle = next(w for w in walk(widget) if isinstance(w,Gtk.ToggleButton))
    popup = next(w for w in walk(widget) if isinstance(w,Gtk.Popover))
    toggle.set_active(True)
    yield
    assert popup.get_mapped(), name
    yield from screen(popup, name+'-open')
    rows = [w for w in walk(popup) if w.get_css_name()=='row' and w.get_mapped()]
    assert len(rows)>1, name
    target = rows[-1]
    for state, title in ((Gtk.StateFlags.PRELIGHT,'hover'),(Gtk.StateFlags.SELECTED,'selected'),(Gtk.StateFlags.BACKDROP,'backdrop')):
        target.set_state_flags(state, False)
        yield from screen(popup, name+'-'+title)
        target.unset_state_flags(state)
    popup.popdown()
    yield


def button_states(widget, name):
    buttons=[w for w in walk(widget) if isinstance(w,Gtk.Button) and w.get_mapped() and w.is_sensitive()]
    for state,title in ((Gtk.StateFlags.PRELIGHT,'hover'),(Gtk.StateFlags.ACTIVE,'pressed')):
        for button in buttons:button.set_state_flags(state,False)
        yield from screen(widget,name+'-'+title)
        for button in buttons:button.unset_state_flags(state)


def context_menu(widget, name):
    widget.grab_focus()
    assert widget.activate_action('menu.popup',None)
    yield
    popup=next(w for w in walk(widget) if isinstance(w,Gtk.Popover) and w.get_mapped())
    yield from screen(popup,name)
    popup.popdown()
    yield


def workflow():
    yield
    app.sidebar.set_visible(True)
    app.store.conversation('A second conversation')
    app.refresh_history()
    app.append('YOU','A user message with readable text.')
    app.append('BUDDY','**Assistant reply.**\nText, `code`, and bullet points.')
    app.append('ERROR','Connection refused: synthetic error')
    app.input.get_buffer().set_text('An unsent message')
    for key in THEMES:
        app.apply_theme(key)
        yield from screen(app.window,'conversation')
        yield from button_states(app.window,'conversation-buttons')
        yield from context_menu(app.input,'composer-context-menu')
        yield from dropdown(app.mode,'mode')
        for widget,name in ((app.theme_menu,'theme-menu'),(app.model_menu.button,'model-menu')):
            widget.popup()
            yield from screen(widget.get_popover(),name)
            if widget is app.model_menu.button:yield from dropdown(app.model_menu.thinking,'toolbar-thinking')
            widget.popdown()
        # Exercise the conversation options and computer options popovers.
        for index,widget in enumerate([w for w in walk(app.window) if isinstance(w,Gtk.MenuButton) and w not in (app.theme_menu,app.model_menu.button)]):
            widget.popup()
            yield from screen(widget.get_popover(),'other-menu-'+str(index))
            widget.popdown()
        settings = app.settings_dialog().controller
        yield from screen(settings.window,'settings',True)
        yield from screen(app.window,'conversation-backdrop')
        yield from dropdown(settings.theme,'appearance')
        help_window = settings.show_help()
        yield from screen(help_window,'help',True)
        help_window.destroy()
        settings.show_it()
        yield from screen(settings.window,'it-setup',True)
        settings.close()
        setup = app.providers_dialog().controller
        setup.connection_expander.set_expanded(True)
        yield from screen(setup.window,'provider-setup',True)
        yield from button_states(setup.window,'provider-buttons')
        yield from dropdown(setup.provider,'provider')
        yield from dropdown(setup.thinking,'setup-thinking')
        setup.advanced.set_expanded(True)
        yield from screen(setup.window,'provider-advanced',True)
        yield from dropdown(setup.protocol,'protocol')
        setup.close()
        connected = app.mcp_dialog().controller
        yield from screen(connected.window,'connected-apps',True)
        connected.stack.set_visible_child_name('saved')
        connected.advanced.set_expanded(True)
        yield from screen(connected.window,'connections-advanced',True)
        yield from button_states(connected.window,'connections-buttons')
        editor = connected.edit()
        yield from screen(editor.window,'connection-editor',True)
        yield from dropdown(editor.transport,'transport')
        yield from dropdown(editor.auth,'authentication')
        for expander in walk(editor.window):
            if isinstance(expander,Gtk.Expander):expander.set_expanded(True)
        yield from screen(editor.window,'connection-editor-advanced',True)
        editor.close()
        keys = connected.client_keys()
        yield from screen(keys.window,'client-keys',True)
        yield from dropdown(keys.group,'client-service')
        keys.window.destroy()
        token_dialog = connected.token_dialog({'name':'Example service'},MCPConfig('fixture','Example service'))
        yield from screen(token_dialog,'token-entry')
        token_dialog.destroy()
        connected.close()
        for factory,name in ((app.memory_dialog,'memory'),(app.audit_dialog,'activity'),
                             (app.rename_conversation_dialog,'rename'),(app.delete_conversation_dialog,'delete')):
            window = factory()
            yield from screen(window,name,True)
            window.destroy()
        access = ComputerAccessDialog(app)
        yield from screen(access.window,'computer-access-pending')
        access.finish(error='fixture')
        yield from screen(access.window,'computer-access-error')
        access.close()
        token = StopToken()
        def approval():
            try:app.approve('desktop.input','{"action":"click","x":100,"y":200}',token)
            except Cancelled:pass
        worker = threading.Thread(target=approval,daemon=True)
        worker.start()
        while not app.pending:yield
        review = app.pending[0][1]
        yield from screen(review,'approval')
        for expander in walk(review):
            if isinstance(expander,Gtk.Expander):expander.set_expanded(True)
        yield from screen(review,'approval-details',True)
        token.stop()
        while app.pending:yield
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(key+': audited',flush=True)


steps = workflow()
started = time.monotonic()
def tick():
    try:
        if time.monotonic()-started > 180:raise TimeoutError('Theme surface audit')
        next(steps)
        return True
    except StopIteration:pass
    except Exception:
        import traceback
        result['error'] = traceback.format_exc()
    app.quit()
    return False

GLib.timeout_add(100,tick)
try:app.run([])
finally:
    for _done,_window,token in app.pending:token.stop()
    app.mcp.close();app.store.close();profile.cleanup()
if getattr(app,'startup_error',None):result['error']=app.startup_error
result['seconds']=round(time.monotonic()-started,2)
(root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'surfaces':len(result['surfaces']), 'failures':result['failures'], 'error':result.get('error')},indent=2))
raise SystemExit(1 if result['failures'] or 'error' in result else 0)
