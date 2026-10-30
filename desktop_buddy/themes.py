"""Complete application palettes, independent of the desktop's GTK theme."""

# Every palette defines the same roles. Light themes come first, then dark
# ones; the menu shows them in this order.
THEMES = {
    'sage': ('Sage · light', {
        'background': '#f8f7f3', 'text': '#263b35', 'muted': '#536259',
        'surface': '#ffffff', 'sidebar': '#eeefe8', 'subtle': '#f1f3ed',
        'control': '#eaece5', 'hover': '#dde4d9', 'selection': '#dce5d6',
        'soft_hover': '#eef2e9', 'user': '#e9eee4', 'border': '#bdccbb',
        'line': '#d8ded3', 'accent': '#315c49', 'accent_hover': '#234735',
        'on_accent': '#ffffff', 'author': '#42604b', 'mark': '#e1e9d9',
        'danger': '#9c4139', 'danger_fill': '#a03f35', 'on_danger': '#ffffff',
        'error_bg': '#fff1e9', 'error_text': '#6c392d', 'error_border': '#d8afa0',
    }),
    'paper': ('Paper · light', {
        'background': '#fafbfe', 'text': '#202b3d', 'muted': '#536176',
        'surface': '#ffffff', 'sidebar': '#eef1f7', 'subtle': '#f0f3f9',
        'control': '#e9edf5', 'hover': '#dce4f2', 'selection': '#dce6fa',
        'soft_hover': '#edf2fc', 'user': '#e9effa', 'border': '#b9c6db',
        'line': '#d3dbea', 'accent': '#315a9e', 'accent_hover': '#23457f',
        'on_accent': '#ffffff', 'author': '#365785', 'mark': '#e1eafa',
        'danger': '#a03936', 'danger_fill': '#a03936', 'on_danger': '#ffffff',
        'error_bg': '#fff0ed', 'error_text': '#783b32', 'error_border': '#dcb4aa',
    }),
    'linen': ('Linen · light', {
        'background': '#faf6f0', 'text': '#3a2e26', 'muted': '#6b5c50',
        'surface': '#ffffff', 'sidebar': '#f1ebe2', 'subtle': '#f4efe7',
        'control': '#ece5db', 'hover': '#e0d6c8', 'selection': '#e6d9c6',
        'soft_hover': '#f2ebe0', 'user': '#efe7dc', 'border': '#c9b8a3',
        'line': '#dfd4c5', 'accent': '#8a4b2a', 'accent_hover': '#6e3a1f',
        'on_accent': '#ffffff', 'author': '#7a4529', 'mark': '#f0e2d1',
        'danger': '#9b3a33', 'danger_fill': '#9b3a33', 'on_danger': '#ffffff',
        'error_bg': '#fdeeea', 'error_text': '#6e362e', 'error_border': '#d9aba1',
    }),
    'slate': ('Slate · light', {
        'background': '#f5f6f8', 'text': '#1f2429', 'muted': '#555d66',
        'surface': '#ffffff', 'sidebar': '#eaedf1', 'subtle': '#eff1f4',
        'control': '#e4e8ed', 'hover': '#d6dce4', 'selection': '#d9e0e8',
        'soft_hover': '#edf0f4', 'user': '#e8ecf1', 'border': '#b4bec9',
        'line': '#d3d9e0', 'accent': '#2c5d7c', 'accent_hover': '#1f4760',
        'on_accent': '#ffffff', 'author': '#355a73', 'mark': '#dde8f0',
        'danger': '#a0393a', 'danger_fill': '#a0393a', 'on_danger': '#ffffff',
        'error_bg': '#fcefed', 'error_text': '#733633', 'error_border': '#dbb0ab',
    }),
    'midnight': ('Midnight · dark', {
        'background': '#181e29', 'text': '#edf1f7', 'muted': '#b4c0d2',
        'surface': '#222b39', 'sidebar': '#1c2431', 'subtle': '#222c3a',
        'control': '#2e3a4b', 'hover': '#3b4b61', 'selection': '#354964',
        'soft_hover': '#2b394c', 'user': '#28364a', 'border': '#526781',
        'line': '#41516a', 'accent': '#a9c8ff', 'accent_hover': '#c4d9ff',
        'on_accent': '#152c4d', 'author': '#b9d3ff', 'mark': '#304463',
        'danger': '#ffb4aa', 'danger_fill': '#ffb4aa', 'on_danger': '#4b1714',
        'error_bg': '#3f292c', 'error_text': '#ffcbc2', 'error_border': '#875354',
    }),
    'ember': ('Ember · dark', {
        'background': '#1f1a18', 'text': '#f3ebe4', 'muted': '#c4b5ab',
        'surface': '#2a2320', 'sidebar': '#241e1b', 'subtle': '#2a2421',
        'control': '#3a302b', 'hover': '#4a3e38', 'selection': '#4b3a31',
        'soft_hover': '#352c27', 'user': '#332924', 'border': '#7a6557',
        'line': '#4d3f37', 'accent': '#f0a878', 'accent_hover': '#f7bf98',
        'on_accent': '#3a1d0c', 'author': '#f3b58a', 'mark': '#4a3326',
        'danger': '#ffb3a8', 'danger_fill': '#ffb3a8', 'on_danger': '#4a1511',
        'error_bg': '#432a28', 'error_text': '#ffc9bf', 'error_border': '#8a5752',
    }),
    'forest': ('Forest · dark', {
        'background': '#151e1a', 'text': '#e8f0ea', 'muted': '#aebfb4',
        'surface': '#1e2a24', 'sidebar': '#19241f', 'subtle': '#1f2b25',
        'control': '#2a3a32', 'hover': '#37493f', 'selection': '#334b3f',
        'soft_hover': '#273830', 'user': '#25352d', 'border': '#5a7466',
        'line': '#3b4f45', 'accent': '#9fd8b4', 'accent_hover': '#bce6ca',
        'on_accent': '#0f2e1d', 'author': '#b0e0c2', 'mark': '#2c4538',
        'danger': '#ffb4a8', 'danger_fill': '#ffb4a8', 'on_danger': '#4a1511',
        'error_bg': '#3c2a29', 'error_text': '#ffcbc1', 'error_border': '#86554f',
    }),
    'contrast': ('High contrast · dark', {
        'background': '#000000', 'text': '#ffffff', 'muted': '#d0d0d0',
        'surface': '#111111', 'sidebar': '#0a0a0a', 'subtle': '#121212',
        'control': '#262626', 'hover': '#383838', 'selection': '#2f3f55',
        'soft_hover': '#1e1e1e', 'user': '#1c1c1c', 'border': '#8a8a8a',
        'line': '#4a4a4a', 'accent': '#ffd54a', 'accent_hover': '#ffe27a',
        'on_accent': '#000000', 'author': '#ffe27a', 'mark': '#3a3100',
        'danger': '#ff9a8a', 'danger_fill': '#ff9a8a', 'on_danger': '#000000',
        'error_bg': '#3a1a16', 'error_text': '#ffc2b5', 'error_border': '#a05a50',
    }),
}
DEFAULT_THEME = 'sage'
ROLES = tuple(THEMES[DEFAULT_THEME][1])


def theme_css(name):
    palette = THEMES.get(name, THEMES[DEFAULT_THEME])[1]
    colors = '\n'.join(f'@define-color buddy_{role} {value};' for role, value in palette.items())
    return colors + '\n' + STYLES


def contrast_ratio(foreground, background):
    """WCAG contrast ratio between two '#rrggbb' colors (1 = none, 21 = black on white)."""
    def luminance(color):
        channels = []
        for index in (1, 3, 5):
            value = int(color[index:index + 2], 16) / 255
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        red, green, blue = channels
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


STYLES = '''
/* Native widgets also use these aliases, including their backdrop states. */
@define-color theme_bg_color @buddy_background;
@define-color theme_fg_color @buddy_text;
@define-color theme_base_color @buddy_surface;
@define-color theme_text_color @buddy_text;
@define-color theme_selected_bg_color @buddy_selection;
@define-color theme_selected_fg_color @buddy_text;
@define-color theme_unfocused_bg_color @buddy_background;
@define-color theme_unfocused_fg_color @buddy_text;
@define-color theme_unfocused_base_color @buddy_surface;
@define-color theme_unfocused_text_color @buddy_text;
@define-color theme_unfocused_selected_bg_color @buddy_selection;
@define-color theme_unfocused_selected_fg_color @buddy_text;
@define-color insensitive_fg_color @buddy_muted;
@define-color borders @buddy_line;

window, window.background, headerbar, .toolbar {
    background: @buddy_background; color: @buddy_text;
}
headerbar { box-shadow: none; border-bottom: 1px solid @buddy_line; }
/* Desktop-theme color fades can pass through unreadable foreground/background
   combinations when a button changes state or a light/dark palette switches. */
button, row, modelbutton, checkbutton, entry { transition: none; }
/* DropDown popups use listview.view, not ListBox's list node. Pair text and
   backgrounds on every native list surface, including unfocused popups. */
list, listview, gridview, columnview, treeview, iconview {
    background: @buddy_surface; color: @buddy_text; border-color: @buddy_line;
}
row { background: transparent; color: @buddy_text; }
row:hover { background: @buddy_soft_hover; color: @buddy_text; }
row:selected, selection { background: @buddy_selection; color: @buddy_text; }
row:selected:hover { background: @buddy_selection; color: @buddy_text; }
modelbutton { background: transparent; color: @buddy_text; }
modelbutton:hover, modelbutton:selected { background: @buddy_selection; color: @buddy_text; }
/* Native text-editing menus otherwise fade shortcut text to 55% opacity. */
popover accelerator, .dim-label { color: @buddy_muted; opacity: 1; }
button { background-image: none; box-shadow: none; border: 0; border-radius: 10px;
         padding: 10px 14px; background-color: @buddy_control; color: @buddy_text; }
button:hover { background-color: @buddy_hover; }
button.flat { background: transparent; }
button.flat:hover { background: @buddy_control; }
button.suggested-action { background: @buddy_accent; color: @buddy_on_accent; font-weight: 600; }
button.suggested-action:hover { background: @buddy_accent_hover; }
button.destructive-action { background: @buddy_danger_fill; color: @buddy_on_danger; }
button:checked, button:active { background: @buddy_selection; color: @buddy_text; }
button:disabled { background: @buddy_control; color: @buddy_muted; opacity: 1; }
label:disabled, entry:disabled, entry text:disabled { color: @buddy_muted; opacity: 1; }
button.link, link, link:visited { color: @buddy_accent; }
button.link:hover, link:hover { color: @buddy_accent_hover; }
button:focus-visible, entry:focus-within, checkbutton:focus-visible {
    outline: 2px solid @buddy_accent; outline-offset: 2px;
}
.sidebar { background: @buddy_sidebar; color: @buddy_text; padding: 20px 14px; }
.brand { font-size: 20px; font-weight: 700; letter-spacing: -.4px; }
.muted { color: @buddy_muted; }
.eyebrow { color: @buddy_muted; font-size: 11px; font-weight: 600; letter-spacing: 1.2px; }
.conversation-title { font-size: 15px; font-weight: 600; }
.sidebar list, .sidebar row { background: transparent; color: @buddy_text; }
.sidebar row { border-radius: 10px; padding: 12px 10px; margin: 3px 0; }
.sidebar row:selected { background: @buddy_selection; color: @buddy_text; }
.sidebar row:hover { background: @buddy_hover; }
entry, searchentry, dropdown > button, spinbutton {
    background: @buddy_surface; color: @buddy_text; border: 1px solid @buddy_line; border-radius: 9px;
}
entry, searchentry { padding: 6px 8px; }
entry text, searchentry text { color: @buddy_text; background: transparent; }
entry text placeholder, searchentry text placeholder { color: @buddy_muted; opacity: 1; }
entry image, searchentry image { color: @buddy_muted; opacity: 1; }
dropdown arrow { background: none; color: @buddy_text; }
checkbutton, expander { color: @buddy_text; }
checkbutton check, checkbutton radio { background: @buddy_surface; color: @buddy_text; border-color: @buddy_border; }
checkbutton check:checked, checkbutton radio:checked { background: @buddy_accent; color: @buddy_on_accent; }
separator { background: @buddy_line; min-height: 1px; }
/* The line between the sidebar and the conversation: drag it to resize the sidebar. */
paned.sidebar-split > separator { background: @buddy_line; min-width: 1px; min-height: 0; }
paned.sidebar-split > separator:hover { background: @buddy_border; }
.toolbar { padding: 12px 20px; }
.conversation { padding: 20px 0; }
.message { padding: 12px 0; }
.message-body { font-size: 16px; line-height: 1.4; }
.user-message { background: @buddy_user; border-radius: 18px; padding: 18px 22px; margin-left: 48px; }
.message-author { font-size: 12px; font-weight: 700; color: @buddy_author; }
button.copy-message { min-height: 22px; padding: 2px 8px; font-size: 12px; opacity: .85; }
button.copy-message:hover { opacity: 1; }
.tool-card { border: 1px solid @buddy_line; border-radius: 12px; padding: 12px 16px; background: @buddy_subtle; }
.tool-card label { font-size: 13px; }
.error-card { border-color: @buddy_error_border; background: @buddy_error_bg; color: @buddy_error_text; }
.error-card .muted, .error-card expander { color: @buddy_error_text; }
.composer { background: @buddy_surface; border: 1px solid @buddy_border; border-radius: 20px; padding: 4px 12px 12px; box-shadow: 0 4px 18px alpha(@buddy_text, .05); }
.resize-handle { min-height: 14px; }
.resize-grip { background: @buddy_border; border-radius: 999px; min-width: 44px; min-height: 4px; }
.resize-handle:hover .resize-grip { background: @buddy_muted; }
textview, textview text { background: transparent; color: @buddy_text; }
textview { padding: 4px; }
.composer textview { font-size: 16px; }
.composer dropdown > button { border: 0; background: @buddy_soft_hover; padding: 6px 10px; }
.attachment { background: @buddy_control; border-radius: 8px; padding: 5px; }
.welcome-title { font-size: 36px; font-weight: 600; letter-spacing: -1px; }
.welcome { padding: 16px 6px; }
.compact .welcome { padding: 4px 6px; }
.compact .welcome-mark { padding: 8px; }
.compact .welcome-title { font-size: 28px; }
.welcome-subtitle { font-size: 16px; color: @buddy_muted; }
.welcome-mark { background: @buddy_mark; color: @buddy_accent; border-radius: 20px; padding: 18px; }
.suggestion { background: @buddy_surface; border: 1px solid @buddy_line; padding: 16px; }
.suggestion:hover { background: @buddy_soft_hover; }
button.jump-to-end { background: @buddy_surface; color: @buddy_text; border: 1px solid @buddy_border; border-radius: 999px; padding: 10px; min-width: 20px; min-height: 20px; box-shadow: 0 3px 12px alpha(@buddy_text, .18); }
button.jump-to-end:hover { background: @buddy_soft_hover; }
.status { color: @buddy_muted; font-size: 12px; }
.status.attention { color: @buddy_danger; font-size: 13px; font-weight: 700; }
.stop-button { color: @buddy_danger; }
.settings-section { background: @buddy_surface; border: 1px solid @buddy_line; border-radius: 12px; padding: 16px; }
.step-title { font-size: 16px; font-weight: 600; }
.model-choice { padding: 6px; border-radius: 10px; background: @buddy_surface; }
.model-choice row { border-radius: 8px; }
.model-choice row:selected { background: @buddy_selection; color: @buddy_text; }
.model-choice row:hover { background: @buddy_soft_hover; }
popover contents { background: @buddy_background; color: @buddy_text; padding: 12px; border: 1px solid @buddy_line; }
popover arrow { background: @buddy_background; border-color: @buddy_line; }
menubutton.model-menu > button { background: @buddy_surface; color: @buddy_text; border: 1px solid @buddy_line; padding: 6px 10px; }
menubutton.model-menu > button:hover { background: @buddy_soft_hover; }
.model-title { font-size: 13px; font-weight: 600; }
.model-detail { font-size: 12px; color: @buddy_muted; }
checkbutton.menu-choice { padding: 6px 4px; border-radius: 8px; }
checkbutton.menu-choice:hover { background: @buddy_soft_hover; }
checkbutton.menu-choice:checked { background: @buddy_mark; }
/* The jump button indicates more content; native overshoot glows look like dividers. */
scrolledwindow > overshoot, scrolledwindow > undershoot { background: none; box-shadow: none; border: none; }
button.screen-preview { background: @buddy_surface; border: 1px solid @buddy_line; border-radius: 12px; padding: 6px 14px 6px 6px; }
button.screen-preview:hover { background: @buddy_soft_hover; border-color: @buddy_border; }
.screen-preview picture { border-radius: 7px; }
.preview-title { font-size: 14px; font-weight: 600; }
tooltip, tooltip.background { background: @buddy_surface; color: @buddy_text; border: 1px solid @buddy_border; }
.lightbox { background: alpha(#101814, .95); padding: 18px 24px 24px; }
.lightbox-caption { color: #ffffff; font-size: 15px; font-weight: 600; }
.lightbox-hint { color: alpha(#ffffff, .7); font-size: 13px; }
button.lightbox-close { background: alpha(#ffffff, .14); color: #ffffff; }
button.lightbox-close:hover { background: alpha(#ffffff, .26); }
'''
