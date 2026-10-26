"""Small client for a local ComfyUI service.

The server is fixed to numeric loopback, responses are bounded, and workflow outputs
are copied into the active workspace with exclusive file creation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

import requests

from .security import Cancelled, Denied, StopToken, local_url


class ComfyUIError(RuntimeError):
    pass


class ComfyUI:
    def __init__(self, base_url='http://127.0.0.1:8188'):
        if not local_url(base_url):
            raise Denied('ComfyUI must use a numeric loopback URL.')
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False

    def request(self, method, path, token, body=None, limit=2 * 1024 * 1024):
        token.check()
        try:
            with self.session.request(method, self.base_url + path, json=body,
                    timeout=(3, 10), stream=True, allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise ComfyUIError(f'ComfyUI returned HTTP {response.status_code} for {path}.')
                data = bytearray()
                for chunk in response.iter_content(16384):
                    token.check()
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ComfyUIError(f'ComfyUI response exceeded {limit // 1024} KiB.')
                return bytes(data)
        except requests.RequestException as exc:
            token.check()
            raise ComfyUIError(f'Cannot reach ComfyUI at {self.base_url} ({type(exc).__name__}).') from exc

    def json(self, method, path, token, body=None, limit=2 * 1024 * 1024):
        raw = self.request(method, path, token, body, limit)
        try:
            result = json.loads(raw or b'{}')
        except (ValueError, UnicodeDecodeError) as exc:
            raise ComfyUIError('ComfyUI returned invalid JSON.') from exc
        return result

    def status(self, token):
        stats = self.json('GET', '/system_stats', token)
        queue = self.json('GET', '/queue', token)
        system = stats.get('system', {})
        devices = []
        for device in stats.get('devices', [])[:16]:
            devices.append({key: device.get(key) for key in
                ('name', 'type', 'vram_total', 'vram_free', 'torch_vram_total', 'torch_vram_free')
                if key in device})
        return {
            'connected': True,
            'url': self.base_url,
            'version': system.get('comfyui_version'),
            'python_version': system.get('python_version'),
            'devices': devices,
            'running': len(queue.get('queue_running', [])),
            'pending': len(queue.get('queue_pending', [])),
        }

    @staticmethod
    def workflow(source, overrides):
        try:
            workflow = json.loads(source)
        except (ValueError, TypeError) as exc:
            raise ComfyUIError('Workflow must be valid ComfyUI API-format JSON.') from exc
        if not isinstance(workflow, dict) or not workflow or len(workflow) > 1000:
            raise ComfyUIError('Workflow must be a non-empty object with at most 1000 nodes.')
        for node_id, node in workflow.items():
            if not isinstance(node_id, str) or not isinstance(node, dict) or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
                raise ComfyUIError('Workflow is not in API format; export it with “Save (API Format)”.')
        for node_id, inputs in (overrides or {}).items():
            if node_id not in workflow:
                raise ComfyUIError(f'Override refers to missing node {node_id}.')
            if not isinstance(inputs, dict):
                raise ComfyUIError(f'Overrides for node {node_id} must be an object.')
            workflow[node_id]['inputs'].update(inputs)
        return workflow

    @staticmethod
    def history_entry(history, prompt_id):
        if not isinstance(history, dict):
            raise ComfyUIError('ComfyUI history response had an unexpected shape.')
        return history.get(prompt_id)

    def run(self, source, overrides, output_directory, token, timeout=900):
        workflow = self.workflow(source, overrides)
        prompt_id = str(uuid.uuid4())
        client_id = 'desktop-buddy-' + uuid.uuid4().hex
        try:
            submitted = self.json('POST', '/prompt', token,
                {'prompt': workflow, 'client_id': client_id, 'prompt_id': prompt_id})
            if submitted.get('prompt_id') != prompt_id:
                raise ComfyUIError('ComfyUI did not confirm the requested job identifier.')
            deadline = time.monotonic() + max(10, min(int(timeout), 3600))
            entry = None
            while time.monotonic() < deadline:
                token.wait(.35)
                history = self.json('GET', '/history/' + prompt_id, token, limit=8 * 1024 * 1024)
                entry = self.history_entry(history, prompt_id)
                if entry:
                    break
            if not entry:
                raise TimeoutError(f'ComfyUI job {prompt_id} did not finish within {timeout} seconds.')
        except (Cancelled, TimeoutError):
            # This job was authorized as one operation. Cancelling its external
            # compute is part of stopping that operation.
            try:self.cancel(prompt_id, StopToken())
            except Exception:pass
            raise
        status = entry.get('status', {})
        if status.get('status_str') != 'success' or status.get('completed') is not True:
            messages = status.get('messages', [])
            raise ComfyUIError('ComfyUI job failed: ' + json.dumps(messages, ensure_ascii=False)[:4000])
        output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        copied = []
        for node_id, output in entry.get('outputs', {}).items():
            if not isinstance(node_id,str) or not node_id or any(c in node_id for c in ('/','\\','\x00')) or node_id in ('.','..'):
                raise ComfyUIError('ComfyUI returned an unsafe output node identifier.')
            for image in output.get('images', []) if isinstance(output, dict) else []:
                filename = image.get('filename', '')
                if not filename or Path(filename).name != filename:
                    raise ComfyUIError('ComfyUI returned an unsafe output filename.')
                query = urlencode({'filename': filename, 'subfolder': image.get('subfolder', ''), 'type': image.get('type', 'output')})
                data = self.request('GET', '/view?' + query, token, limit=40 * 1024 * 1024)
                destination = output_directory / f'{prompt_id[:8]}-{node_id}-{filename}'
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    with os.fdopen(fd, 'wb') as output_file:
                        output_file.write(data)
                        output_file.flush()
                        os.fsync(output_file.fileno())
                except Exception:
                    try:destination.unlink()
                    except FileNotFoundError:pass
                    raise
                copied.append({'path': str(destination), 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'node': node_id})
        return {'prompt_id': prompt_id, 'status': 'complete', 'outputs': copied,
            'verified': True, 'server_status': status}

    def cancel(self, prompt_id, token):
        try:
            uuid.UUID(prompt_id)
        except ValueError as exc:
            raise ComfyUIError('ComfyUI job ID must be a UUID.') from exc
        result = self.json('POST', '/api/jobs/' + prompt_id + '/cancel', token)
        return {'prompt_id': prompt_id, 'cancelled': bool(result.get('cancelled', True))}
