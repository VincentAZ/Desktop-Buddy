from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import requests
from .security import Denied, Cancelled
from .comfyui import ComfyUI

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: object
    mutation: bool = False
    risk: str = 'read'


def schema(properties,required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
S={'type':'string'}
I={'type':'integer'}
N={'type':'number'}
B={'type':'boolean'}

def validate(value,spec,path='arguments'):
    typ=spec.get('type')
    types={'object':dict,'array':list,'string':str,'integer':int,'number':(int,float),'boolean':bool}
    if typ in types and (not isinstance(value,types[typ]) or typ in ('integer','number') and isinstance(value,bool)):
        raise ValueError(f'{path} must be {typ}.')
    if 'enum' in spec and value not in spec['enum']:raise ValueError(f'{path} must be one of {spec["enum"]}.')
    if isinstance(value,dict):
        for k in spec.get('required',[]):
            if k not in value:raise ValueError(f'Missing {path}.{k}.')
        for k,v in value.items():
            if k not in spec.get('properties',{}):
                if spec.get('additionalProperties') is False:raise ValueError(f'Unknown argument {path}.{k}.')
            else:validate(v,spec['properties'][k],path+'.'+k)
    if isinstance(value,list):
        if len(value)>200:raise ValueError('Too many array elements.')
        for v in value:validate(v,spec.get('items',{}),path+'[]')
    if isinstance(value,str) and len(value)>200000:raise ValueError('Tool argument exceeds 200000 characters.')

class Files:
    def __init__(self,policy):self.policy=policy

    def parent(self,raw):
        # Open each directory without following links. dir_fd operations pin parents
        # against concurrent rename/symlink replacement after policy evaluation.
        p=self.policy.path(raw)
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in p.parts[1:-1]:
                nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=nextfd
            return fd,p.name,p
        except Exception:os.close(fd);raise

    def read(self,path):
        parent,name,p=self.parent(path)
        try:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):raise Denied('Only regular files may be read.')
                raw=f.read(100001)
            return {'path':str(p),'content':raw[:100000].decode('utf-8',errors='replace'),'truncated':len(raw)>100000,'sha256':hashlib.sha256(raw).hexdigest() if len(raw)<=100000 else None}
        finally:os.close(parent)

    def write(self,path,content,expected_sha256=''):
        parent,name,p=self.parent(path);tmp='.buddy-'+uuid.uuid4().hex
        permissions=0o600
        try:
            if expected_sha256:
                actual=self.read(path)['sha256']
                if actual!=expected_sha256:raise ValueError('File changed since observation. Read it again before writing.')
            try:
                st=os.stat(name,dir_fd=parent,follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):raise Denied('Refusing to replace a link or non-regular file.')
                permissions=stat.S_IMODE(st.st_mode) & 0o777
            except FileNotFoundError:pass
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            with os.fdopen(fd,'wb') as f:
                f.write(content.encode());f.flush();os.fchmod(f.fileno(),permissions);os.fsync(f.fileno())
            os.replace(tmp,name,src_dir_fd=parent,dst_dir_fd=parent)
            checkfd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            with os.fdopen(checkfd,'rb') as f:data=f.read()
            return {'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'verified':data==content.encode()}
        finally:
            try:os.unlink(tmp,dir_fd=parent)
            except FileNotFoundError:pass
            os.close(parent)

    def list(self,path='.'):
        p=self.policy.path(path)
        return {'path':str(p),'entries':[{'name':x.name,'directory':x.is_dir(follow_symlinks=False),'symlink':x.is_symlink()} for x in sorted(os.scandir(p),key=lambda v:v.name)[:500]]}

    def mkdir(self,path):
        parent,name,p=self.parent(path)
        try:os.mkdir(name,0o700,dir_fd=parent);return {'path':str(p),'verified':p.is_dir()}
        finally:os.close(parent)

    def move(self,path,destination):
        a,src,p=self.parent(path)
        try:b,dst,q=self.parent(destination)
        except Exception:
            os.close(a)
            raise
        try:
            if not stat.S_ISREG(os.stat(src,dir_fd=a,follow_symlinks=False).st_mode):raise Denied('Move handles regular files only.')
            # link fails if destination exists. No overwrite race; same-filesystem only.
            os.link(src,dst,src_dir_fd=a,dst_dir_fd=b,follow_symlinks=False)
            os.unlink(src,dir_fd=a)
            return {'path':str(q),'verified':q.exists() and not p.exists()}
        finally:os.close(a);os.close(b)

    def delete(self,path):
        parent,name,p=self.parent(path)
        try:
            if not stat.S_ISREG(os.stat(name,dir_fd=parent,follow_symlinks=False).st_mode):raise Denied('Delete handles one regular file only; no recursive deletion.')
            os.unlink(name,dir_fd=parent)
            return {'path':str(p),'verified':not p.exists()}
        finally:os.close(parent)

    def search(self,path='.',query=''):
        p=self.policy.path(path);matches=[];visited=0
        for root,dirs,files in os.walk(p,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith('.')][:100]
            for name in files:
                visited+=1
                if query.lower() in name.lower():matches.append(str(Path(root)/name))
                if len(matches)>=200 or visited>=10000:return {'matches':matches,'truncated':True}
        return {'matches':matches,'truncated':False}


def run_process(argv,cwd,token,timeout=60,local_only=False):
    if not argv or not all(isinstance(x,str) and '\x00' not in x for x in argv):raise ValueError('argv must be a non-empty list of strings.')
    timeout=max(1,min(timeout,180))
    env={'PATH':os.environ.get('PATH',os.defpath),'LANG':'C.UTF-8','HOME':str(Path.home())}
    if local_only:
        if not shutil.which('bwrap'):raise Denied('Local-only shell requires bubblewrap. No unsandboxed fallback is allowed.')
        # Hide session sockets, user files and host process table. Only workspace writable.
        command=['bwrap','--die-with-parent','--new-session','--unshare-net','--unshare-pid','--unshare-ipc',
            '--ro-bind','/','/','--tmpfs','/home','--tmpfs','/root','--tmpfs','/run','--tmpfs','/tmp',
            '--dev','/dev','--proc','/proc','--bind',str(cwd),str(cwd),'--chdir',str(cwd),
            '--clearenv','--setenv','PATH',os.defpath,'--setenv','LANG','C.UTF-8','--setenv','HOME',str(cwd),'--',*argv]
    else:command=argv
    token.check()
    start=time.monotonic()
    with tempfile.TemporaryFile() as output:
        proc=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            token.track(proc)
            while proc.poll() is None:
                token.wait(.03)
                if time.monotonic()-start>timeout:
                    token._kill(proc);proc.wait();raise TimeoutError(f'Command exceeded {timeout}s and its process group was killed.')
                if os.fstat(output.fileno()).st_size>2*1024*1024:
                    token._kill(proc);proc.wait();raise ValueError('Command output exceeded 2 MiB; process group killed.')
            token.check();output.seek(0);data=output.read(60001)
            return {'exit_code':proc.returncode,'output':data[:60000].decode(errors='replace'),'truncated':len(data)>60000,'elapsed_seconds':round(time.monotonic()-start,3),'verified':proc.returncode==0}
        finally:
            # Also kill inherited descendants after the parent exits; no background jobs.
            token._kill(proc);proc.wait();token.untrack(proc)

class Registry:
    def __init__(self,policy,store,desktop=None,accessibility=None,clipboard=None,browser=None):
        self.policy=policy;self.store=store;self.desktop=desktop;self.accessibility=accessibility
        self.clipboard=clipboard;self.browser=browser;self.tools={};self.files=Files(policy)
        self.comfyui=ComfyUI(store.get('comfyui_url','http://127.0.0.1:8188'))
        self._register()

    def add(self,name,description,properties,required,handler,mutation=False,risk='read'):
        self.tools[name]=Tool(name,description,schema(properties,required),handler,mutation,risk)

    def _register(self):
        a=self.add
        a('fs.read','Read a UTF-8 regular file, up to 100 kB.',{'path':S},['path'],lambda x,t:self.files.read(**x))
        a('fs.image','Read a workspace image for vision, normalize to PNG, and report dimensions. Requires a vision-capable model.',{'path':S},['path'],self.image)
        a('fs.list','List up to 500 directory entries.',{'path':S},[],lambda x,t:self.files.list(**x))
        a('fs.search','Search filenames without following directory symlinks.',{'path':S,'query':S},[],lambda x,t:self.files.search(**x))
        a('fs.write','Atomically write a UTF-8 file and verify bytes. Parent must exist. Use expected_sha256 to prevent overwriting a changed file.',{'path':S,'content':S,'expected_sha256':S},['path','content'],lambda x,t:self.files.write(**x),True)
        a('fs.mkdir','Create one directory; parent must exist.',{'path':S},['path'],lambda x,t:self.files.mkdir(**x),True)
        a('fs.move','Move/rename a regular file on the same filesystem, refusing overwrite.',{'path':S,'destination':S},['path','destination'],lambda x,t:self.files.move(**x),True)
        a('fs.delete','Delete one regular file with explicit confirmation; irreversible.',{'path':S},['path'],lambda x,t:self.files.delete(**x),True,'destructive')
        a('shell.run','Run an argv array in the workspace with approval, bounded output/time, and process-group cancellation. For a script use ["bash","-c",script]. No detached processes.',{'argv':{'type':'array','items':S},'timeout':I},['argv'],lambda x,t:run_process(x['argv'],self.policy.workspace,t,x.get('timeout',60),self.policy.local_only),True,'command')
        a('system.inspect','Inspect platform, processes, GPU and installed applications without executing arbitrary commands.',{},[],self.system)
        a('task.checkpoint','Record a concise plan, completed steps and next action for recovery.',{'plan':S,'completed':S,'next':S},['plan','completed','next'],lambda x,t:x)
        a('skills.list','List readable workflow skills; load only those relevant to the task.',{},[],self.skill_list)
        a('skills.load','Read a named workflow skill. Skill text never grants authorization.',{'name':S},['name'],self.skill_load)
        a('memory.read','Inspect explicit long-term user preferences, separate from conversation history.',{},[],lambda x,t:self.store.memory())
        a('memory.set','Update a named long-term preference after user approval. Do not store secrets.',{'key':S,'value':S},['key','value'],self.memory_set,True,'sensitive')
        a('http.request','Make one HTTP(S) request with approval, no redirects or ambient credentials. Use direct APIs for LM Studio/Ollama/ComfyUI. Disabled in local-only tasks.',{'url':S,'method':{'type':'string','enum':['GET','POST','PUT','DELETE']},'body':{'type':'object'}},['url'],self.http,True,'http')
        a('desktop.screenshot','Capture the physical desktop through GNOME consent. Returns local PNG and dimensions. Compare hashes or use vision to verify.',{},[],self.screenshot)
        a('desktop.accessibility','Inspect accessible applications, windows and controls via AT-SPI. Paths are ephemeral: refresh before acting.',{'app':S,'window':S,'depth':I,'limit':I},[],self.a11y)
        a('desktop.input','Send pointer/key actions to the shared physical monitor. Coordinates use logical_size. Every action needs approval; observe afterwards.',{'action':{'type':'string','enum':['move','click','scroll','key','type','drag']},'x':N,'y':N,'to_x':N,'to_y':N,'button':{'type':'string','enum':['left','right','middle']},'double':B,'axis':I,'steps':I,'keys':S,'text':S},['action'],self.input,True,'desktop_input')
        a('desktop.accessible_action','Focus or invoke a named accessible action after verifying expected element name.',{'path':S,'action':S,'expected_name':S},['path','action','expected_name'],self.accessible_action,True,'desktop_input')
        a('desktop.window','GNOME keyboard window operations on the currently focused window. Verify focus before using. Exact geometry requires supported compositor tooling.',{'action':{'type':'string','enum':['maximize','restore','move','resize','next']}},['action'],self.window,True,'desktop_input')
        a('desktop.clipboard_read','Read clipboard text; may contain sensitive data. Explicit permission required.',{},[],lambda x,t:self._clipboard('read',None),False,'sensitive')
        a('desktop.clipboard_write','Replace clipboard text with explicit permission.',{'text':S},['text'],lambda x,t:self._clipboard('write',x['text']),True,'desktop_input')
        a('app.launch','Launch an installed application by registered desktop ID (from system.inspect). No arbitrary command or hidden argument.',{'desktop_id':S},['desktop_id'],self.launch,True,'launch')
        a('browser.navigate','Open an isolated visible Chrome window with a workspace profile and verify URL/title via CDP. Never connects to your personal profile.',{'url':S},['url'],self.navigate,True,'network')
        a('browser.inspect','Read the isolated browser URL/title and bounded page text.',{},[],self.browser_inspect,False,'network')
        a('browser.action','Click/fill a selector in the isolated browser; every action approved. Inspect resulting page to verify.',{'selector':S,'action':{'type':'string','enum':['click','fill']},'text':S},['selector','action'],self.browser_action,True,'network')
        a('comfyui.status','Inspect the local ComfyUI version, GPU devices, and queue without changing it.',{},[],lambda x,t:self.comfyui.status(t))
        a('comfyui.run','Run an exported ComfyUI API workflow from the workspace, optionally override node inputs, wait for verified completion, and copy images into a workspace directory. One approval covers the complete job.',{'workflow_path':S,'overrides':{'type':'object','additionalProperties':True},'output_directory':S,'timeout':I},['workflow_path','output_directory'],self.comfyui_run,True,'compute')
        a('comfyui.run_batch','Run up to 50 prompt values through one exported ComfyUI API workflow. One approval covers the exact batch; every job is verified and outputs are copied into the workspace.',{'workflow_path':S,'prompt_node_id':S,'input_name':S,'prompts':{'type':'array','items':S},'overrides':{'type':'object','additionalProperties':True},'output_directory':S,'timeout_per_job':I},['workflow_path','prompt_node_id','input_name','prompts','output_directory'],self.comfyui_batch,True,'compute')
        a('comfyui.cancel','Cancel one ComfyUI job by UUID with approval.',{'prompt_id':S},['prompt_id'],lambda x,t:self.comfyui.cancel(x['prompt_id'],t),True,'compute')

    def discover(self,query=''):
        terms=query.lower().split()
        selected=[t for t in self.tools.values() if not terms or any(q in (t.name+' '+t.description).lower() for q in terms)]
        return [{'name':t.name,'description':t.description,'parameters':t.parameters,'mutation':t.mutation,'approval':t.risk} for t in selected[:12]]

    def execute(self,name,args,token,tid=''):
        if name not in self.tools:raise ValueError('Unknown tool; use discover_tools to find exact tool names.')
        tool=self.tools[name];validate(args,tool.parameters)
        # JSON copy detaches the exact authorized arguments from mutable caller objects.
        args=json.loads(json.dumps(args))
        self.policy.authorize(name,args,tool.mutation,tool.risk,token)
        token.check();self.store.audit(tid,'tool_started',{'name':name,'arguments':args})
        try:
            result=tool.handler(args,token)
            token.check();result=self.store.redactor.clean(result)
            self.store.audit(tid,'tool_finished',{'name':name,'result':result})
            return result
        except Exception as e:
            self.store.audit(tid,'tool_error',{'name':name,'error':str(e)})
            raise

    def system(self,x,token):
        import platform
        import gi
        gi.require_version('Gio','2.0')
        from gi.repository import Gio
        apps=[{'id':a.get_id(),'name':a.get_display_name()} for a in Gio.AppInfo.get_all() if a.should_show()]
        return {'platform':platform.platform(),'desktop':os.environ.get('XDG_CURRENT_DESKTOP'),'session':os.environ.get('XDG_SESSION_TYPE'),
            'applications':apps,'executables':{n:shutil.which(n) for n in ['docker','ollama','lms','cursor','google-chrome','ptyxis','nvidia-smi','bwrap']},
            'memory':Path('/proc/meminfo').read_text()[:1200],'load':os.getloadavg()}

    def skill_list(self,x,t):
        folder=Path(__file__).parent/'skills'
        return [{'name':p.stem,'summary':p.read_text().splitlines()[0]} for p in sorted(folder.glob('*.md'))]

    def skill_load(self,x,t):
        name=x['name']
        if '/' in name or '\\' in name or name.startswith('.'):raise ValueError('Use a skill name from skills.list.')
        p=Path(__file__).parent/'skills'/(name+'.md')
        return {'name':name,'untrusted_workflow':p.read_text()[:24000]}

    def memory_set(self,x,t):
        memory=self.store.memory();memory[x['key']]=x['value'];self.store.save_memory(memory)
        return {'saved':x['key']}

    def http(self,x,t):
        from urllib.parse import urlsplit
        u=urlsplit(x['url'])
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:raise ValueError('Use an HTTP(S) URL without embedded credentials.')
        s=requests.Session();s.trust_env=False
        with s.request(x.get('method','GET'),x['url'],json=x.get('body'),timeout=(5,15),stream=True,allow_redirects=False) as r:
            data=bytearray()
            for chunk in r.iter_content(4096):
                t.check();data.extend(chunk)
                if len(data)>100000:break
            return {'status':r.status_code,'body':bytes(data[:100000]).decode(errors='replace'),'truncated':len(data)>100000,'verified':200<=r.status_code<300}

    def image(self,x,t):
        from PIL import Image
        parent,name,p=self.files.parent(x['path'])
        try:
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            with os.fdopen(fd,'rb') as source:
                st=os.fstat(source.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_size>40*1024*1024:
                    raise ValueError('Image must be a regular file smaller than 40 MiB.')
                with Image.open(source) as im:
                    if im.width*im.height>40000000:raise ValueError('Image exceeds 40 million pixels.')
                    original=im.size;im=im.convert('RGB');im.thumbnail((1920,1920))
                    out=self.store.root/'images'/(uuid.uuid4().hex+'.png');out.parent.mkdir(parents=True,exist_ok=True)
                    t.check();im.save(out);os.chmod(out,0o600)
                    return {'path':str(out),'source':str(p),'original_size':original,'width':im.width,'height':im.height}
        finally:os.close(parent)

    def screenshot(self,x,t):
        if not self.desktop:raise RuntimeError('Desktop portal backend is unavailable.')
        return self.desktop.screenshot(self.store.root/'screenshots'/(uuid.uuid4().hex+'.png'),t)

    def a11y(self,x,t):
        if not self.accessibility:raise RuntimeError('AT-SPI is unavailable.')
        return self.accessibility.inspect(x.get('app',''),max(0,min(x.get('depth',3),24)),max(1,min(x.get('limit',300),800)),x.get('window',''))

    def input(self,x,t):
        if not self.desktop:raise RuntimeError('Desktop backend unavailable.')
        return self.desktop.input(x,t)

    def accessible_action(self,x,t):
        if not self.accessibility:raise RuntimeError('Accessibility unavailable.')
        return self.accessibility.act(**x)

    def window(self,x,t):
        keys={'maximize':'Super+Up','restore':'Super+Down','move':'Alt+F7','resize':'Alt+F8','next':'Alt+Tab'}
        return self.input({'action':'key','keys':keys[x['action']]},t)

    def _clipboard(self,operation,text):
        if not self.clipboard:raise RuntimeError('Clipboard requires the running desktop UI.')
        return self.clipboard(operation,text)

    def launch(self,x,t):
        import gi
        gi.require_version('Gio','2.0')
        from gi.repository import Gio
        app=Gio.DesktopAppInfo.new(x['desktop_id'])
        if not app:raise ValueError('Application is not installed or desktop ID is unknown.')
        t.check();ok=app.launch([],None)
        return {'launched':bool(ok),'verified':False,'next':'Inspect desktop.accessibility or screenshot for the application.'}

    def navigate(self,x,t):
        if not self.browser:raise RuntimeError('Browser control unavailable.')
        return self.browser.navigate(x['url'],t)

    def browser_inspect(self,x,t):
        if not self.browser:raise RuntimeError('Browser control unavailable.')
        return self.browser.inspect(t)

    def browser_action(self,x,t):
        if not self.browser:raise RuntimeError('Browser control unavailable.')
        return self.browser.action(x,t)

    def comfyui_run(self,x,t):
        source=self.files.read(x['workflow_path'])
        if source['truncated']:raise ValueError('ComfyUI workflow exceeds the 100 kB workflow limit.')
        destination=self.policy.path(x['output_directory'],write=True)
        if not destination.is_relative_to(self.policy.workspace):
            raise Denied('ComfyUI outputs must stay inside the active workspace.')
        return self.comfyui.run(source['content'],x.get('overrides',{}),destination,t,x.get('timeout',900))

    def comfyui_batch(self,x,t):
        prompts=x['prompts']
        if not prompts or len(prompts)>50:raise ValueError('ComfyUI batches require 1–50 prompts.')
        source=self.files.read(x['workflow_path'])
        if source['truncated']:raise ValueError('ComfyUI workflow exceeds the 100 kB workflow limit.')
        destination=self.policy.path(x['output_directory'],write=True)
        if not destination.is_relative_to(self.policy.workspace):
            raise Denied('ComfyUI outputs must stay inside the active workspace.')
        results=[]
        for index,prompt in enumerate(prompts):
            t.check()
            overrides=json.loads(json.dumps(x.get('overrides',{})))
            overrides.setdefault(x['prompt_node_id'],{})[x['input_name']]=prompt
            try:
                result=self.comfyui.run(source['content'],overrides,destination,t,x.get('timeout_per_job',900))
                results.append({'index':index,'prompt':prompt,'status':'complete','prompt_id':result['prompt_id'],'outputs':result['outputs']})
            except Cancelled:raise
            except Exception as error:
                results.append({'index':index,'prompt':prompt,'status':'failed','error':str(error)})
        complete=sum(item['status']=='complete' for item in results)
        return {'jobs':results,'complete':complete,'failed':len(results)-complete,
            'verified':complete==len(results)}
