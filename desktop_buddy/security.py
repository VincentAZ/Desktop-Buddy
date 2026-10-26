"""Host-enforced policy. Model text can never create an authorization."""
from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import threading
from urllib.parse import urlsplit

class Cancelled(Exception):
    pass

class Denied(Exception):
    pass

class StopToken:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.RLock()
        self.processes = set()

    def check(self):
        if self.event.is_set():
            raise Cancelled('Task stopped. No further actions will run.')

    def wait(self, seconds):
        if self.event.wait(seconds):
            self.check()

    def track(self, process):
        with self.lock:
            if self.event.is_set():
                self._kill(process)
                self.check()
            self.processes.add(process)

    def untrack(self, process):
        with self.lock:
            self.processes.discard(process)

    @staticmethod
    def _kill(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def stop(self):
        with self.lock:
            self.event.set()
            for p in list(self.processes):
                self._kill(p)

class Redactor:
    def __init__(self):
        self.secrets = set()
        self.lock = threading.Lock()

    def register(self, secret):
        if secret:
            with self.lock:
                self.secrets.add(secret)

    def text(self, value):
        value = str(value)
        if value.lstrip().startswith(('{','[')):
            try:
                parsed=json.loads(value)
                if isinstance(parsed,(dict,list)):
                    return json.dumps(self.clean(parsed),ensure_ascii=False)
            except (ValueError,RecursionError):pass
        with self.lock:
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)(Bearer\s+)[\w.\-+/=]+', r'\1[REDACTED]', value)
        value = re.sub(r'\b(?:sk-|sk-ant-|xai-)[A-Za-z0-9_\-]{12,}', '[REDACTED]', value)
        value = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*["\x27]?)[^\s,"\x27}]+', r'\1[REDACTED]', value)
        value = re.sub(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----.*?-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', value, flags=re.S)
        return value

    def stream_text(self, value):
        # Withhold partial known credentials at chunk boundaries. Ordinary logging
        # uses text(); this additionally protects the live streaming preview.
        value=str(value)
        with self.lock:
            secrets=list(self.secrets)
        for secret in secrets:
            for length in range(min(len(secret),len(value)),3,-1):
                if value.endswith(secret[:length]):
                    value=value[:-length]+'[REDACTED]';break
        value=re.sub(r'(?:sk-|sk-ant-|xai-)[A-Za-z0-9_\-]*$', '[REDACTED]',value)
        return self.text(value)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {k: ('[REDACTED]' if k.lower() in {'api_key','apikey','password','access_token','refresh_token','authorization','client_secret','secret','token'} else self.clean(v)) for k,v in value.items()}
        return value


def local_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http','https') or p.username or p.password or p.fragment:
        return False
    try:
        return ipaddress.ip_address(p.hostname or '').is_loopback
    except ValueError:
        return False


def validate_endpoint(url, local=False):
    p = urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('Use an HTTP(S) base URL without credentials, query or fragment.')
    if local and not local_url(url):
        raise Denied('Local providers require a numeric loopback URL (127.0.0.1 or [::1]).')
    if not local_url(url) and p.scheme != 'https':
        raise Denied('Remote endpoints must use HTTPS.')

class Policy:
    def __init__(self, workspace, mode='plan', local_only=True, approve=None, protected=()):
        if mode not in ('plan', 'execute', 'yolo'):
            raise ValueError('Unknown task mode.')
        self.workspace = Path(workspace).resolve()
        self.mode = mode
        self.local_only = local_only
        self.approve = approve or (lambda *args: False)
        self.protected = [Path(p).resolve() for p in protected]

    def request_approval(self, name, detail, token):
        """YOLO is an explicit user grant, never an instruction from tool data."""
        token.check()
        allowed = self.mode == 'yolo' or self.approve(name, detail, token)
        token.check()
        return allowed

    def path(self, raw, write=False):
        p = Path(raw).expanduser()
        p = (self.workspace/p if not p.is_absolute() else p).resolve()
        sensitive = any(part in {'.ssh','.gnupg','.codex','.hermes','.aws','.azure','keyrings'} for part in p.parts)
        sensitive |= p.name in {'.env','credentials','auth.json','config.json'} and not p.is_relative_to(self.workspace)
        sensitive |= p.name == '.env' or p.name.endswith(('.pem','.key'))
        if sensitive or any(p == x or p.is_relative_to(x) for x in self.protected):
            raise Denied('Credential/app-state paths are not accessible through model tools.')
        return p

    def authorize(self, name, args, mutation, risk, token):
        token.check()
        if mutation and self.mode == 'plan':
            raise Denied('Just discuss prevents changes. Choose Help me do it to allow actions.')
        if self.local_only and risk in {'network','http'}:
            raise Denied('Local-only mode blocks network operations initiated by Desktop Buddy.')
        outside = False
        if name.startswith('fs.') or name.startswith('comfyui.'):
            for key in ('path','destination','workflow_path','output_directory'):
                if key in args:
                    p = self.path(args[key], write=mutation)
                    outside |= not p.is_relative_to(self.workspace)
        if risk in {'command','destructive','desktop_input','launch','http','network','sensitive','compute'} or outside:
            detail = json.dumps(args,ensure_ascii=False,indent=2)
            if not self.request_approval(name, detail, token):
                raise Denied('User denied this action.')
        token.check()
