"""Plain-language presentation of host state; never an authorization policy."""
import json


ACTION_NAMES={
    'buddy.status':'Check Buddy settings','buddy.help':'Look up Buddy help',
    'attachments.list':'Check attached files','attachments.read':'Read an attached file',
    'attachments.copy':'Make a working copy',
    'fs.read':'Read a file','fs.list':'Look through a folder','fs.search':'Find files',
    'fs.write':'Save a file','fs.move':'Move a file','fs.mkdir':'Create a folder',
    'fs.delete':'Delete a file','fs.image':'Look at an image','shell.run':'Run a computer task',
    'desktop.connect':'Ask to use your computer','desktop.screenshot':'Look at your screen',
    'desktop.input':'Use your keyboard or mouse','desktop.accessibility':'Look at open apps',
    'desktop.accessible_action':'Use a control in an app','desktop.window':'Arrange a window',
    'desktop.set_text':'Enter text in an app',
    'desktop.clipboard_read':'Read copied text','desktop.clipboard_write':'Copy text',
    'app.launch':'Open an app','browser.navigate':'Open a web page','browser.inspect':'Read a web page',
    'browser.action':'Use a web page','http.request':'Contact an online service',
    'mcp.servers':'Check your connected apps','mcp.tools':'Check what an app can do',
    'mcp.call':'Work in a connected app','mcp.resources':'Find information in an app',
    'mcp.read_resource':'Read information from an app','system.inspect':'Check this computer',
    'task.checkpoint':'Save progress','skills.list':'Prepare the next step','skills.load':'Check instructions',
    'memory.read':'Check your preferences','memory.set':'Remember a preference',
    'comfyui.status':'Check image creation','comfyui.run':'Create images',
    'comfyui.run_batch':'Create several images','comfyui.cancel':'Stop image creation',
    'discover_tools':'Prepare the next step','Cloud data disclosure':'Use your online assistant',
}


def action_name(name):
    return ACTION_NAMES.get(name,'Work on your request')


def preference_text(value):
    """Readable legacy structured preferences; untouched values retain their type."""
    if isinstance(value,str):return value
    if value is None:return 'Not specified'
    if isinstance(value,bool):return 'Yes' if value else 'No'
    if isinstance(value,list):return '\n'.join('• '+preference_text(item).replace('\n','\n  ') for item in value)
    if isinstance(value,dict):return '\n'.join(str(key)+': '+preference_text(item).replace('\n','\n  ') for key,item in value.items())
    return str(value)


def approval_summary(name,detail,app_names=None,*,purpose='',workspace='',local_only=False):
    """Present exact values without guessing what an arbitrary operation will do."""
    try:arguments=json.loads(detail)
    except (ValueError,TypeError):return str(detail)
    if not isinstance(arguments,dict):return str(detail)
    if name=='shell.run':
        summary='Buddy proposes:\n'+purpose if purpose else 'Buddy has not explained this computer task. Ask for a clearer explanation before allowing it.'
        if workspace:summary+='\n\nStarting folder: '+workspace
        timeout=max(1,min(arguments.get('timeout',120),3600))
        summary+='\nTime limit: '+str(timeout)+' seconds'
        summary+='\nInternet access is blocked for this task.' if local_only else '\nThese instructions can use the internet and work outside this folder.'
        return summary+'\n\nThe exact instructions are available in Technical details.'
    window_keyboard=name=='desktop.input' and arguments.get('keyboard_scope')=='window'
    if window_keyboard:
        arguments={k:v for k,v in arguments.items() if k not in ('keyboard_scope','target_path','expected_name')}
    titles={'server':'App','name':'Requested operation','arguments':'Details','to':'To','cc':'Cc','bcc':'Bcc',
            'subject':'Subject','body':'Message','text':'Text','destination':'Save to','path':'File',
            'url':'Website','desktop_id':'Application','selector':'Page control','keys':'Keys',
            'id':'Item','content':'Contents','expected_sha256':'Previously checked file fingerprint',
            'expected_window':'Window','expected_name':'Control','target_path':'Control reference'}
    def lines(value,depth=0):
        result=[];prefix='  '*depth
        if isinstance(value,dict):
            for key,item in value.items():
                title=titles.get(key,key.replace('_',' ').capitalize())
                if isinstance(item,(dict,list)):
                    result.append(prefix+title+':');result.extend(lines(item,depth+1))
                else:
                    if key=='server' and app_names:item=app_names.get(item,item)
                    shown=item if isinstance(item,str) else json.dumps(item,ensure_ascii=False)
                    if name=='mcp.call' and key=='name' and depth==0:shown=shown.replace('_',' ').capitalize()
                    result.append(prefix+title+': '+shown)
        elif isinstance(value,list):
            for index,item in enumerate(value,1):
                result.append(prefix+str(index)+'.')
                result.extend(lines(item,depth+1))
        else:
            result.append(prefix+(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)))
        return result
    summary='\n'.join(lines(arguments))
    if window_keyboard:summary+='\n\nKeys go to the active part of this window. Buddy will stop if another window takes focus.'
    return summary


def friendly_error(error):
    """Give a useful next step without claiming that an interrupted effect failed."""
    raw=str(error);value=raw.casefold()
    if 'keyring' in value:
        return ('Buddy could not unlock your saved sign-in.',
                'Unlock your computer’s password keyring, then try again.')
    if any(term in value for term in ('401','invalid_api_key','invalid api key','unauthorized','authentication')):
        return ('Your assistant or app connection needs attention.',
                'Open Settings or Connected apps to check the affected connection.')
    if any(term in value for term in ('429','rate limit','quota','insufficient credits','billing')):
        return ('The online service cannot take this request right now.',
                'You may have reached its usage limit. Wait before trying again; check the service account if this continues.')
    if 'context budget' in value:
        return ('Buddy needs more room to finish this task.',
                'Your progress is saved. Check any completed changes before retrying. Ask IT to adjust the assistant for longer tasks.')
    if any(term in value for term in ('connection refused','connectionrefusederror','connection error','network is unreachable','name resolution','timed out','timeout')):
        return ('Buddy could not reach the service.',
                'Check your connection. Before repeating a change, check whether it already happened.')
    if 'offline' in value or 'local-only' in value:
        return ('Internet access is turned off for Buddy.',
                'Open Settings if you want to allow online requests.')
    if any(term in value for term in ('not ready','not available','no such file','not installed','unavailable')):
        return ('Something Buddy needs is not available.',
                'Check Settings or Connected apps. A feature marked unavailable needs a Buddy update, not technical setup from you.')
    if any(term in value for term in ('cancelled','canceled','task stopped','denied','not approved')):
        return ('This step was stopped or not allowed.',
                'Completed changes are kept. Review what happened before continuing.')
    return ('Buddy could not finish this step.',
            'Review the latest result before continuing. Technical details are available below if needed.')


def activity_summary(who,text):
    try:data=json.loads(text)
    except (ValueError,TypeError):data=None
    if who=='ERROR':return friendly_error(text)
    if isinstance(data,list):return ('Preparing the next steps','Buddy is choosing how to carry out your request.')
    if isinstance(data,dict):
        name=data.get('name') or data.get('tool');args=data.get('arguments',{})
        if name=='run_tool' and isinstance(args,dict):name=args.get('name');args=args.get('arguments',{})
        if name:
            target=(args.get('path') or args.get('url') or args.get('server')) if isinstance(args,dict) else None
            return action_name(name),str(target or 'Details are recorded for this step.')
        if data.get('isError') or data.get('error'):return friendly_error(data.get('error') or text)
        if data.get('verified') is True:return 'Step checked','Buddy checked the result of this step.'
        if data.get('verified') is False:return 'Result needs checking','The action returned a result, but its effect has not been confirmed.'
        if 'exit_code' in data:
            return ('Computer task returned a result','It finished without reporting an error.' if data['exit_code']==0 else 'It reported a problem. Buddy needs to check the result.')
    return 'Step returned a result','Buddy will use this information to continue. This does not by itself confirm the task is finished.'
