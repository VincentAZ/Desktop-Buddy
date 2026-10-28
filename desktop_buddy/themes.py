"""Complete application palettes, independent of the desktop's GTK theme."""

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
}
DEFAULT_THEME = 'sage'


def theme_css(name):
    palette = THEMES.get(name, THEMES[DEFAULT_THEME])[1]
    colors = '\n'.join(f'@define-color buddy_{role} {value};' for role, value in palette.items())
    return colors + '\n' + STYLES


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
.sidebar { background: @buddy_sidebar; color: @buddy_text; border-right: 1px solid @buddy_line; padding: 20px 14px; }
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
.toolbar { padding: 12px 20px; }
.conversation { padding: 20px 0; }
.message { padding: 12px 0; }
.message-body { font-size: 16px; line-height: 1.4; }
.user-message { background: @buddy_user; border-radius: 18px; padding: 18px 22px; margin-left: 48px; }
.message-author { font-size: 12px; font-weight: 700; color: @buddy_author; }
button.copy-message { min-height: 22px; min-width: 22px; padding: 2px; opacity: .7; }
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
