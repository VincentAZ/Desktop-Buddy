"""Shared, offline product help and a bounded view of current application state.

No credential lookup, network request, desktop initialization or configuration
mutation is permitted here. Model names and connection names remain untrusted data.
"""
from . import __version__
from .state import PROVIDERS


TOPICS = {
    'getting-started': ('Start using Buddy',
        'Open Buddy from its desktop or app-menu icon. There is no Desktop Buddy account or login. '
        'Type what you want in the message box and choose Send. The first time, choose an assistant: '
        'Settings → IT setup → Add or configure a vendor…, pick a service (OpenAI, Anthropic, xAI, Moonshot, OpenRouter, '
        'or LM Studio / Ollama on this computer), enter its key if the service needs one, pick a model and press Use model. '
        'Whoever set up the computer usually does this once; it can be changed at any time.'),
    'sessions': ('Run concurrent conversations',
        'Choose New conversation (Ctrl+N) while a task is working, then send another request. '
        'Switch conversations in the sidebar to read replies, send follow-ups or review approvals. '
        'Drafts and attachments stay with their conversation. Stop cancels only the selected conversation; '
        'Stop everything cancels all tasks. Each conversation has its own browser profile. '
        'Hide the browser window in Settings for parallel browsing. Tasks using the shared screen, mouse, '
        'keyboard or a visible browser take turns until the task using them finishes. '
        'Files and connected app accounts are shared; choose separate folders or filenames for unrelated work.'),
    'models': ('Change the assistant',
        'Open Settings → IT setup. Choose a saved assistant from the model menu, or Add or configure a vendor… '
        'to connect another service. A running task keeps the assistant it started with; the new choice applies '
        'to the next message. Model settings are remembered on this computer.'),
    'connections': ('Connect an app',
        'Choose Connect apps, then Add an app. Pick the app and choose Connect. Most apps (Notion, Linear, Jira, '
        'Stripe, Vercel, Canva, Dropbox and many more) open a sign-in page in your browser; sign in, approve, '
        'and come back. GitHub asks you to paste a personal access token. Google apps, Slack, Asana, HubSpot, Zendesk, '
        'Box and Figma need a one-time sign-in key entered by the Desktop Buddy team or IT (Advanced → App sign-in keys); '
        'until then they show “needs a one-time setup”. You can also paste any server address under “Have a server address?”. '
        'Your apps lists saved connections: Turn off keeps the sign-in, Remove forgets it. '
        'Gmail and Calendar give Buddy real mailbox and calendar access: search and read mail, draft, send, label, and manage events. '
        'Buddy uses a connected app before the browser. If an app is not connected, Buddy may use the browser or the desktop instead.'),
    'computer': ('Use apps on your computer',
        'Buddy can look at your screen, click, type and use controls in your apps. The first time, your computer shows a '
        'permission window: click the picture of your screen, keep “Allow Remote Interaction” on, and click Share. '
        'Tick the option to remember it and Buddy will not ask again. You can also do this ahead of time: '
        'Computer access in the sidebar, or the screen icon beside the message box → Let Buddy use my computer. '
        'Buddy prefers the app’s own controls when it can read them and falls back to looking at the screen for other apps.'),
    'browser': ('Browse the web',
        'Buddy has its own browser window, separate from yours, with no saved logins. It is used for websites '
        'that are not connected as apps. Under Settings you can hide that window so browsing happens in the background. '
        'For a site you use often, connecting it as an app is faster and more reliable.'),
    'files': ('Work with files',
        'Use Attach a file beside the message box to hand Buddy documents or images. Buddy keeps a saved copy '
        'and edits a working copy; your original stays unchanged. New files land in Buddy’s work folder (Work folder '
        'in the sidebar). Change the folder under Settings → Where Buddy saves your work. Excel, Word, PDF, CSV and '
        'text are read directly; scanned PDFs need text recognition first.'),
    'permissions': ('Choose how much Buddy does on its own',
        'The menu beside Send has three choices. Discuss only: Buddy reads and explains, changes nothing. '
        'Ask before acting: Buddy works on files in its work folder and asks you before commands, typing, clicking, '
        'connected apps or sending anything online. Just do it: Buddy acts without asking; you confirm this once, and Stop '
        'always works. Settings → Keep Buddy offline blocks online requests and controlling other apps; it needs a local model.'),
    'stop': ('Stop or recover a task',
        'Use Stop to cancel the selected conversation’s task. Stop everything cancels every task, disconnects computer control and closes all Buddy browsers '
        '(Ctrl+Shift+Escape while Buddy is focused). Changes already made stay. “Buddy couldn’t read something it needed” means a '
        'step that read information (an email search, a web page, a file) did not work; Buddy’s reply keeps a note about it '
        'and Continue task tries that read again. After a restart Buddy keeps the conversation and '
        'does not repeat interrupted actions on its own.'),
    'own-code': ('Let Buddy improve itself',
        'Under Settings → IT setup, turn on Let Buddy change its own code. Then ask for the change in plain words, for example '
        '“make the Stop button easier to see” or “update the README”. Buddy edits its own program files or documentation on the main branch, runs its tests for code changes, saves the change (code with a new '
        'version number), publishes it to GitHub with the sign-in already on this computer, and restarts itself after a code change. If other '
        'conversations are still working, a Restart Buddy button appears instead. In Ask before acting each of these steps is '
        'confirmed; Just do it runs them without asking. Discuss only changes nothing, and Keep Buddy offline blocks publishing. '
        'Sign-in keys and git internals stay off limits.'),
    'copy': ('Copy messages',
        'Every message and step has a Copy button. The Copy button in the toolbar copies the whole conversation, '
        'only your messages, only Buddy’s replies, or everything including the steps Buddy took and their results. '
        'Copy what Buddy sees copies all messages in Buddy’s context window: the instructions, messages and step results '
        'that would go to the AI service at the next step, after any shortening to fit, with how much room they use. '
        'Nothing is sent anywhere. Paste with Ctrl+V.'),
    'history': ('History and preferences',
        'Conversations in the sidebar can be searched, reopened, renamed or deleted; everything is stored on this computer. '
        'Things to remember holds preferences you want Buddy to keep. When you use an online assistant, the conversation and '
        'the files, app contents or screenshots used for the task are sent to that service. Keys live in the computer’s keyring.'),
}


def help_topics(query=''):
    terms=query.casefold().split()
    return [{'topic':key,'title':title,'instructions':body} for key,(title,body) in TOPICS.items()
            if not terms or all(term in (key+' '+title+' '+body).casefold() for term in terms)]


def _assistant(config):
    if config is None:return None
    return {'vendor':PROVIDERS[config.id][0], 'model':config.model,
            'configured':bool(config.model), 'local':config.local,
            'tools_enabled':config.use_tools, 'images_enabled':config.use_vision,
            'capabilities_verified':False}


def app_status(registry):
    from .mcp_catalog import CATALOG,consumer_available,consumer_name,availability_text
    registrations=registry.mcp.registrations if registry.mcp is not None else None
    configs=registry.store.configs()
    selected=configs.get(registry.store.get('provider','openai'))
    # Reading LazyBackend attributes normally starts the backend. Inspect its
    # existing instance directly so asking for help never opens a consent prompt.
    desktop=registry.desktop
    if desktop is not None and 'instance' in vars(desktop):desktop=vars(desktop)['instance']
    session=bool(vars(desktop).get('session')) if desktop is not None else False
    pending=bool(vars(desktop).get('pending_session')) if desktop is not None else False
    request=vars(desktop).get('last_request',{}) if desktop is not None else {}
    timed_out=(isinstance(request,dict) and request.get('interface')=='RemoteDesktop' and
               request.get('method')=='Start' and request.get('status')=='timed_out')
    remembered=bool(registry.store.get('desktop_restore_token',''))
    connection_state='connected' if session else 'connecting' if pending else 'timed_out' if timed_out else 'remembered' if remembered else 'not_connected'
    blockers=[]
    if registry.policy.local_only:blockers.append('Keep Buddy offline is on. Turn it off in Settings to allow control of other apps.')
    if registry.policy.mode=='plan':blockers.append('Discuss only blocks changes. Choose Ask before acting or Just do it before requesting computer actions.')
    if not session:
        if pending:blockers.append('A computer permission request is already in progress. Wait for its result; do not start another request.')
        elif timed_out:blockers.append('The computer did not finish connecting. Clicking Share was not enough. Ask the person to try Computer access again rather than repeating the request yourself.')
        elif remembered:blockers.append('The computer remembered screen-sharing permission. desktop.screenshot and desktop.input connect on their own.')
        else:blockers.append('Screen sharing has not been allowed yet. The first desktop.screenshot or desktop.input shows the person a GNOME permission window; they click Share.')
    from .experience import action_name
    failed_sources=getattr(registry,'source_failures',{})
    return {'version':__version__, 'buddy_account_required':False,
            'content_changes_paused':bool(failed_sources),
            'unread_source_steps':sorted({action_name(value['operation']) for value in failed_sources.values()}),
            'active_task_assistant':_assistant(registry.assistant_config),
            'selected_for_next_task':_assistant(selected),
            'mode':{'plan':'Discuss only','execute':'Ask before acting','yolo':'Just do it'}[registry.policy.mode],
            'offline':registry.policy.local_only, 'work_folder':str(registry.policy.workspace),
            'computer_control_connected':session,
            'computer_connection_state':connection_state,
            'computer_control_blockers':blockers,
            'accessibility_backend_configured':registry.accessibility is not None,
            'browser_backend_configured':registry.browser is not None,
            'browser_window_visible':bool(registry.store.get('browser_visible',True)),
            'app_connections_configured':registry.mcp is not None,
            'app_catalog':{'connect_options':[consumer_name(item) for item in CATALOG if consumer_available(item,registrations)],
                           'needs_setup_first':{consumer_name(item):availability_text(item,registrations) for item in CATALOG if not consumer_available(item,registrations)},
                           'note':'Catalog options are apps the person can connect under Connect apps. Check mcp.servers for existing connections.'},
            'limitations':'Configured backends and model flags are not proof of working access. '
                          'Use mcp.servers for saved connections; verify actual task results.',
            'help':'Use buddy.help for exact navigation and instructions.'}
