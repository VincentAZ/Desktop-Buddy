#!/usr/bin/env python3
"""Render real GTK themes, check text contrast, and exercise saved UI choices.

Run with GTK_THEME=Adwaita:dark to reproduce inherited desktop colors. Uses an
isolated profile and no model requests. Screenshots go to .test-data/appearance.
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, GLib, Graphene
from PIL import Image
from desktop_buddy.ui import BuddyApp, content
from desktop_buddy.themes import THEMES

root = Path(__file__).resolve().parents[1]/'.test-data'/'appearance'
root.mkdir(parents=True, exist_ok=True)
restoring = len(sys.argv) > 1 and sys.argv[1] == '--restore'
profile = None if restoring else tempfile.TemporaryDirectory(dir=root)
data = Path(sys.argv[2]) if restoring else Path(profile.name)/'state'
app = BuddyApp(data)
result = {}
if not restoring:
    app.store.set('window_width', 1180)
    app.store.set('window_height', 860)
    config = app.store.configs()['openai']
    config.model = 'gpt-6.1-sol'
    config.reasoning = 'high'
    app.store.save_config(config)
    app.store.set('provider', 'openai')
    app.store.set('local_only', False)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def capture(widget, filename):
    snapshot = Gtk.Snapshot.new()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, float(widget.get_width()), float(widget.get_height()))
    node = snapshot.to_node()
    assert node is not None
    rect = Graphene.Rect()
    rect.init(0, 0, widget.get_width(), widget.get_height())
    path = root/filename
    widget.get_renderer().render_texture(node, rect).save_to_png(str(path))
    return Image.open(path).convert('RGB')


def rgb(color):
    return tuple(round(component*255) for component in (color.red, color.green, color.blue))


def luminance(color):
    values = [c/255 for c in color]
    linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in values]
    return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))


def check_text(widget, background, name, foreground=None):
    foreground = foreground or rgb(widget.get_style_context().get_color())
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    contrast = (light+.05)/(dark+.05)
    assert contrast >= 4.5, (app.theme, name, foreground, background, contrast)
    result.setdefault('contrast', {}).setdefault(app.theme, {})[name] = round(contrast, 2)


def background_at(image, widget, x=2, y=2):
    ok, rect = widget.compute_bounds(app.window)
    assert ok
    return image.getpixel((round(rect.get_x()+x), round(rect.get_y()+y)))


phase = 0
theme_index = 0
started = time.monotonic()


def tick():
    global phase, theme_index, settings
    try:
        if time.monotonic()-started > 30:
            raise TimeoutError(f'phase {phase}')
        if restoring:
            assert app.theme == 'midnight'
            assert app.model_menu.title.get_text() == 'gpt-6.1-sol'
            assert app.model_menu.detail.get_text() == 'OpenAI · Maximum thinking'
            assert app.theme_choices['midnight'].get_active()
            result['theme_and_model_restore_on_restart'] = True
            app.quit()
            return False
        if phase == 0:
            app.sidebar.set_visible(True)
            app.store.conversation('Research and notes')
            app.store.conversation('An unselected conversation')
            app.refresh_history()
            app.store.add_message(app.cid, {'role':'user', 'content':'Make the conversation easy to read.'})
            app.store.add_message(app.cid, {'role':'assistant', 'content':'**Readable in every theme.**\nThe sidebar, model controls, and messages use matching text and background colors.'})
            app.render_history()
            app.append('ERROR', 'Connection refused: synthetic example')
            app.input.get_buffer().set_text('Preserve this unsent draft')
            phase = 1
        elif phase == 1:
            key = list(THEMES)[theme_index]
            app.theme_choices[key].set_active(True)
            assert app.theme == key
            assert app.store.get('theme') == key
            phase = 2
        elif phase == 2:
            image = capture(app.window, app.theme+'.png')
            assert app.window.get_width() <= 1180
            rows = [w for w in walk(app.history) if isinstance(w, Gtk.ListBoxRow)]
            for row in rows:
                text = next(w for w in walk(row) if isinstance(w, Gtk.Label))
                ok, rect = row.compute_bounds(app.window)
                assert ok
                bg = background_at(image, row, rect.get_width()-15, rect.get_height()/2)
                expected = THEMES[app.theme][1]['selection' if row.is_selected() else 'sidebar']
                assert bg == tuple(bytes.fromhex(expected.removeprefix('#'))), (app.theme, bg, expected)
                check_text(text, bg, 'history selected' if row.is_selected() else 'history unselected')
            placeholder = next(w for w in walk(app.history_search) if w.get_css_name() == 'placeholder')
            ok, rect = placeholder.compute_bounds(app.window)
            assert ok
            pixels = image.crop((round(rect.get_x()), round(rect.get_y()), round(rect.get_x()+rect.get_width()), round(rect.get_y()+rect.get_height()))).get_flattened_data()
            # Check the rendered glyph, too: native GTK themes can lower placeholder opacity.
            ink = (max if app.theme == 'midnight' else min)(pixels, key=luminance)
            surface = tuple(bytes.fromhex(THEMES[app.theme][1]['surface'].removeprefix('#')))
            check_text(placeholder, surface, 'search placeholder (rendered)', ink)
            toolbar = app.model_menu.button.get_parent()
            check_text(app.conversation_title, background_at(image, toolbar), 'toolbar title')
            button_bg = background_at(image, app.model_menu.button, 8, app.model_menu.button.get_height()/2)
            check_text(app.model_menu.title, button_bg, 'model')
            check_text(app.model_menu.detail, button_bg, 'provider and thinking')
            input_bg = background_at(image, app.input, 2, app.input.get_height()-2)
            check_text(app.input, input_bg, 'composer')
            assert content(app.input) == 'Preserve this unsent draft'
            assert app.model_menu.button.get_mapped()
            app.model_menu.button.popup()
            phase = 3
        elif phase == 3:
            capture(app.model_menu.popover, app.theme+'-model-menu.png')
            assert app.model_menu.thinking.get_mapped()
            assert app.model_menu.thinking.get_selected_item().get_string() == 'High'
            app.model_menu.popover.popdown()
            settings = app.settings_dialog().controller
            phase = 4
        elif phase == 4:
            capture(settings.window, app.theme+'-settings.png')
            assert settings.theme_ids[settings.theme.get_selected()] == app.theme
            assert app.model_menu.button.get_parent().has_css_class('toolbar')
            settings.close()
            theme_index += 1
            phase = 1 if theme_index < len(THEMES) else 5
        elif phase == 5:
            settings = app.settings_dialog().controller
            settings.theme.set_selected(settings.theme_ids.index('paper'))
            assert app.theme == 'paper' and app.theme_choices['paper'].get_active()
            settings.theme.set_selected(settings.theme_ids.index('midnight'))
            settings.open_model_menu()
            phase = 6
        elif phase == 6:
            assert app.model_menu.thinking.get_mapped()
            app.model_menu.thinking.set_selected(app.model_menu.thinking.values.index('max'))
            assert app.store.configs()['openai'].reasoning == 'max'
            assert app.model_menu.detail.get_text() == 'OpenAI · Maximum thinking'
            app.model_menu.popover.popdown()
            app.sidebar.set_visible(False)
            app.window.set_default_size(760, 640)
            phase = 7
        elif phase == 7:
            capture(app.window, 'midnight-compact.png')
            assert app.window.get_width() <= 760
            for widget in (app.model_menu.button, app.theme_menu):
                ok, rect = widget.compute_bounds(app.window)
                assert ok and rect.get_x() >= 0 and rect.get_x()+rect.get_width() <= app.window.get_width()
            assert content(app.input) == 'Preserve this unsent draft'
            result['toolbar_controls_settings_and_compact_layout'] = True
            app.quit()
            return False
    except Exception as error:
        import traceback
        result.update(error=repr(error), phase=phase, traceback=traceback.format_exc())
        app.quit()
        return False
    return True


GLib.timeout_add(220, tick)
try:
    app.run([])
finally:
    app.mcp.close()
    app.store.close()
if getattr(app, 'startup_error', None):result['error'] = app.startup_error
if not restoring and 'error' not in result:
    restored = subprocess.run([sys.executable, __file__, '--restore', str(data)], capture_output=True, text=True, timeout=20)
    if restored.returncode:
        result['error'] = 'Restart failed: '+restored.stdout+restored.stderr
    else:
        result['theme_and_model_restore_on_restart'] = True
if profile:profile.cleanup()
if not restoring:(root/'result.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
raise SystemExit(1 if 'error' in result else 0)
