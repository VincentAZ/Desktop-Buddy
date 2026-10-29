"""Killable provider I/O. Keys travel over an anonymous pipe, never argv or disk."""
from dataclasses import asdict
import json
import os
import selectors
import subprocess
import sys
import time
from .security import StopToken


def isolated_completion(config,key,messages,tools,token,emit,local_only):
    answer = isolated_operation(config, key, 'complete', token, local_only,
                                {'messages': messages, 'tools': tools}, emit)
    return answer[0], answer[1]


def isolated_operation(config, key, operation, token, local_only=False, payload=None, emit=lambda *_: None):
    from .providers import ProviderError
    token.check()
    env={k:os.environ[k] for k in ('PATH','LANG','SSL_CERT_FILE','SSL_CERT_DIR') if k in os.environ}
    proc=subprocess.Popen([sys.executable,'-m','desktop_buddy.inference'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,start_new_session=True,cwd=os.path.dirname(os.path.dirname(__file__)),env=env)
    selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
    data={'config':asdict(config),'key':key,'operation':operation,'local_only':local_only,**(payload or {})}
    buffer=b'';answer=None;started=time.monotonic()
    try:
        token.track(proc)
        proc.stdin.write(json.dumps(data).encode());proc.stdin.close()
        while True:
            token.check()
            deadline=600 if operation=='complete' else 120
            if time.monotonic()-started>deadline:raise ProviderError(f'Provider {operation} exceeded its {deadline}-second limit.')
            ready=selector.select(.05)
            if not ready:
                if proc.poll() is not None:break
                continue
            chunk=os.read(proc.stdout.fileno(),65536)
            if not chunk:break
            buffer+=chunk
            if len(buffer)>20*1024*1024:raise ProviderError('Inference output exceeded its buffer limit.')
            while b'\n' in buffer:
                line,buffer=buffer.split(b'\n',1)
                event=json.loads(line)
                if event['kind']=='stream':emit('stream',event['data'])
                elif event['kind']=='result':answer=event['data']
                elif event['kind']=='error':raise worker_error(event['data'])
        token.check()
        if answer is None:raise ProviderError('Provider worker exited without a complete response.')
        return answer
    except (BrokenPipeError, ConnectionResetError) as exc:
        token.check()
        raise ProviderError('Provider worker disconnected before completing the request.') from exc
    finally:
        token._kill(proc);proc.wait();token.untrack(proc);selector.close()
        if not proc.stdin.closed:proc.stdin.close()
        proc.stdout.close()


def worker_error(data):
    """Rebuild the worker's exception with its kind, so the agent can tell a reply
    cut at the response limit or a dropped connection (both retryable) from other errors."""
    from .providers import ProviderError,OutputLimitReached,StreamInterrupted
    if not isinstance(data,dict):return ProviderError(str(data))
    if data.get('type')=='OutputLimitReached':return OutputLimitReached(int(data.get('limit') or 0))
    if data.get('type')=='StreamInterrupted':return StreamInterrupted(str(data.get('cause') or 'unknown'))
    return ProviderError(str(data.get('message','')))


def main():
    from .providers import Provider
    from .state import ProviderConfig
    last=[0.0]
    def send(kind,data):
        print(json.dumps({'kind':kind,'data':data}),flush=True)
    def emit(kind,data):
        now=time.monotonic()
        if now-last[0]>.08:last[0]=now;send(kind,data)
    try:
        data=json.load(sys.stdin)
        provider=Provider(ProviderConfig(**data['config']),data['key'])
        operation=data.get('operation','complete')
        if operation=='complete':
            result=provider._complete_http(data['messages'],data['tools'],StopToken(),emit,data['local_only'])
        elif operation=='models':
            result=provider._models_http(StopToken(),data['local_only'])
        elif operation=='local_info':
            result=provider.request('/api/show',StopToken(),{'model':provider.config.model},local_only=True)
        else:raise ValueError('Unknown provider operation.')
        send('result',result)
    except Exception as e:
        send('error',{'message':str(e),'type':type(e).__name__,'limit':getattr(e,'limit',None),'cause':getattr(e,'cause',None)})

if __name__=='__main__':main()
