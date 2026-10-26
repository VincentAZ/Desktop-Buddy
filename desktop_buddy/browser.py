"""Visible Chromium controlled through an inherited CDP pipe: no debug port."""
from __future__ import annotations
import json
import fcntl
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from .security import StopToken

class Browser:
    def __init__(self,root):
        self.root=Path(root);self.proc=None;self.pending={};self.lock=threading.Lock();self.counter=0;self.session=None;self.lifecycle_lock=threading.Lock()

    def start(self,token):
        if self.proc and self.proc.poll() is None:return
        executable=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:raise RuntimeError('Install Chrome/Chromium to use isolated browser control.')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.proc:self.close()
        with self.lifecycle_lock:
            token.check()
            child_read,parent_write=os.pipe();parent_read,child_write=os.pipe()
            try:
                self.proc=subprocess.Popen([sys.executable,'-m','desktop_buddy.browser','--worker',str(child_read),str(child_write),executable,str(self.root)],
                    pass_fds=(child_read,child_write),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            except Exception:
                os.close(parent_write);os.close(parent_read)
                raise
            finally:os.close(child_read);os.close(child_write)
            self.write_fd=parent_write;self.read_fd=parent_read
            self.pending={}
            threading.Thread(target=self._read,args=(parent_read,self.pending),daemon=True,name='browser-cdp').start()
            self.session=None
        try:
            token.check()
            target=self.call('Target.createTarget',{'url':'about:blank'},token,session=False)
            self.session=self.call('Target.attachToTarget',{'targetId':target['targetId'],'flatten':True},token,session=False)['sessionId']
            self.call('Page.enable',{},token)
            self.call('Runtime.enable',{},token)
        except Exception:
            self.close()
            raise

    def _read(self,fd,pending):
        buffer=b''
        try:
            while True:
                part=os.read(fd,65536)
                if not part:break
                buffer+=part
                if len(buffer)>8*1024*1024:break
                while b'\0' in buffer:
                    data,buffer=buffer.split(b'\0',1)
                    if not data:continue
                    msg=json.loads(data)
                    q=pending.get(msg.get('id'))
                    if q:q.put(msg)
        except (OSError,ValueError):pass
        finally:
            for q in list(pending.values()):q.put({'error':{'message':'Browser pipe closed.'}})

    def call(self,method,params,token,session=True):
        token.check()
        with self.lock:
            self.counter+=1;ident=self.counter;out=queue.Queue();self.pending[ident]=out
            request={'id':ident,'method':method,'params':params}
            if session and self.session:request['sessionId']=self.session
            data=json.dumps(request).encode()+b'\0'
            try:
                while data:
                    token.check();count=os.write(self.write_fd,data);data=data[count:]
                until=time.monotonic()+15
                while True:
                    token.check()
                    try:msg=out.get(timeout=.05);break
                    except queue.Empty:
                        if time.monotonic()>until:raise TimeoutError('Browser did not respond in 15 seconds.')
                if 'error' in msg:raise RuntimeError('Browser: '+str(msg['error'].get('message')))
                return msg.get('result',{})
            finally:self.pending.pop(ident,None)

    def evaluate(self,expression,token):
        result=self.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True},token)
        if result.get('exceptionDetails'):raise RuntimeError('Page operation failed: '+result['exceptionDetails'].get('text','JavaScript exception'))
        return result.get('result',{}).get('value')

    def inspect(self,token):
        if not self.proc or self.proc.poll() is not None:raise RuntimeError('No isolated browser is running. Navigate first.')
        return self.evaluate('JSON.parse(JSON.stringify({url:location.href,title:document.title,ready:document.readyState,text:(document.body?.innerText||"").slice(0,20000)}))',token)

    def navigate(self,url,token):
        p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise ValueError('Browser URL must be HTTP(S) without embedded credentials.')
        self.start(token)
        response=self.call('Page.navigate',{'url':url},token)
        if response.get('errorText'):raise RuntimeError('Navigation failed: '+response['errorText'])
        until=time.monotonic()+20
        while time.monotonic()<until:
            token.wait(.1)
            if response.get('loaderId'):
                frame=self.call('Page.getFrameTree',{},token).get('frameTree',{}).get('frame',{})
                if frame.get('loaderId')!=response['loaderId']:continue
            state=self.inspect(token)
            if state['url']!='about:blank' and state['ready']=='complete':
                state['requested_url']=url
                state['verified']=state['url'].rstrip('/')==url.rstrip('/')
                state['note']='Compare final URL and page content; redirects may be legitimate.'
                return state
        raise TimeoutError('Browser page did not finish loading in 20 seconds.')

    def action(self,args,token):
        selector=json.dumps(args['selector']);text=json.dumps(args.get('text',''))
        effect='el.click();' if args['action']=='click' else f'''if(el.type==='password')throw Error('Enter secrets manually');
const setter=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')?.set;
if(!setter)throw Error('Not an input element');setter.call(el,{text});el.dispatchEvent(new Event('input',{{bubbles:true}}));el.dispatchEvent(new Event('change',{{bubbles:true}}));'''
        result=self.evaluate(f'''(()=>{{const el=document.querySelector({selector});if(!el)throw Error('Selector not found');{effect}return {{sent:true,verified:false}};}})()''',token)
        token.wait(.15)
        return {'action':result,'observation':self.inspect(token)}

    def close(self):
        with self.lifecycle_lock:
            proc=self.proc;self.proc=None;self.session=None
            if proc:
                StopToken._kill(proc)
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:pass
                for fd in (self.write_fd,self.read_fd):
                    try:os.close(fd)
                    except OSError:pass


def worker():
    # Duplicate before assigning fixed descriptors; source FDs can be 3 or 4.
    r=fcntl.fcntl(int(sys.argv[2]),fcntl.F_DUPFD_CLOEXEC,10);w=fcntl.fcntl(int(sys.argv[3]),fcntl.F_DUPFD_CLOEXEC,10)
    os.dup2(r,3,inheritable=True);os.dup2(w,4,inheritable=True)
    os.close(r);os.close(w)
    executable=sys.argv[4]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG','XDG_SESSION_TYPE'}}
    os.execve(executable,[executable,'--remote-debugging-pipe','--user-data-dir='+sys.argv[5],
        '--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-sync',
        '--disable-component-update','--disable-default-apps','--window-size=1100,780','about:blank'],env)

if __name__=='__main__':worker()
