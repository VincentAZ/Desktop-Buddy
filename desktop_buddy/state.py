from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import fcntl
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from .security import validate_endpoint

PROVIDERS = {
    'openai': ('OpenAI', 'https://api.openai.com/v1', 'responses', False),
    'anthropic': ('Anthropic', 'https://api.anthropic.com/v1', 'anthropic', False),
    'xai': ('xAI', 'https://api.x.ai/v1', 'responses', False),
    'moonshot': ('Moonshot / Kimi', 'https://api.moonshot.ai/v1', 'chat', False),
    'openrouter': ('OpenRouter', 'https://openrouter.ai/api/v1', 'chat', False),
    'lmstudio': ('LM Studio', 'http://127.0.0.1:1234/v1', 'chat', True),
    'ollama': ('Ollama', 'http://127.0.0.1:11434', 'ollama', True),
}

# The wire value is stored with each model profile. "Provider default" omits
# the parameter, which is safest until a model's supported levels are known.
THINKING_LEVELS = {
    'openai': (
        ('Provider default', ''), ('None', 'none'), ('Minimal', 'minimal'),
        ('Low', 'low'), ('Medium', 'medium'), ('High', 'high'),
        ('Extra high', 'xhigh'), ('Maximum', 'max'),
    ),
    'anthropic': (
        ('Provider default', ''), ('Adaptive', 'adaptive'),
        ('1,024 token budget', '1024'), ('4,096 token budget', '4096'),
        ('8,192 token budget', '8192'), ('16,384 token budget', '16384'),
        ('32,768 token budget', '32768'),
    ),
    'ollama': (
        ('Provider default', ''), ('On', 'true'), ('Off', 'false'),
        ('Low', 'low'), ('Medium', 'medium'), ('High', 'high'),
    ),
}

COMPATIBLE_THINKING_LEVELS = (
    ('Provider default', ''), ('None', 'none'), ('Minimal', 'minimal'),
    ('Low', 'low'), ('Medium', 'medium'), ('High', 'high'),
    ('Extra high', 'xhigh'), ('Maximum', 'max'),
)


def thinking_levels(provider_id, model=''):
    choices = THINKING_LEVELS.get(provider_id, COMPATIBLE_THINKING_LEVELS)
    if provider_id == 'openai' and model == 'gpt-6.1-sol':
        return tuple((title, wire) for title, wire in choices if wire not in ('none','minimal'))
    return choices


def thinking_label(provider_id, value):
    return next((title for title, wire in thinking_levels(provider_id) if wire == value), value or 'Provider default')

@dataclass
class ProviderConfig:
    id: str
    base_url: str
    protocol: str
    local: bool
    model: str = ''
    tools: bool | None = None
    vision: bool | None = None
    context_tokens: int = 32768
    max_output: int = 4096
    reasoning: str = ''
    input_cost: float = 0.0
    output_cost: float = 0.0
    api_version: str = '2026-10-21'
    workspace_id: str = ''

    def __post_init__(self):
        # Official model contract: Sol 6.1 requires Responses for tools, and its
        # lowest reasoning effort is low. Repair old incompatible preferences.
        if self.id == 'openai' and self.model == 'gpt-6.1-sol':
            if self.protocol == 'chat' and self.use_tools:self.protocol = 'responses'
            if self.reasoning in ('none','minimal'):self.reasoning = 'low'

    @property
    def use_tools(self):
        """Unknown means try the API; False is an explicit text-only override."""
        return self.tools is not False

    @property
    def use_vision(self):
        return self.vision is not False

    def validate(self):
        self.__post_init__()
        if self.id not in PROVIDERS:
            raise ValueError('Unknown provider.')
        if self.local != PROVIDERS[self.id][3]:
            raise ValueError('Provider locality cannot be overridden.')
        if self.protocol not in {'responses','chat','anthropic','ollama'}:
            raise ValueError('Unknown wire protocol.')
        validate_endpoint(self.base_url, self.local)
        if not 4096 <= self.context_tokens <= 4000000 or not 128 <= self.max_output < self.context_tokens:
            raise ValueError('Context must be 4096–4000000 tokens; output must fit inside context.')
        import math
        if not all(math.isfinite(x) for x in (self.input_cost, self.output_cost)):
            raise ValueError('Prices must be finite numbers.')
        if self.protocol == 'anthropic' and self.reasoning and self.reasoning != 'adaptive':
            try: budget = int(self.reasoning)
            except ValueError: raise ValueError('Choose a supported thinking budget.') from None
            if not 1024 <= budget < self.max_output:
                raise ValueError('Thinking budget must be at least 1,024 and smaller than the response limit.')
        if self.input_cost < 0 or self.output_cost < 0:
            raise ValueError('Prices cannot be negative.')
        return self

class Store:
    def __init__(self, root, redactor):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.root,0o700)
        self.lock_file=open(self.root/'instance.lock','a')
        os.chmod(self.root/'instance.lock',0o600)
        try:fcntl.flock(self.lock_file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            raise RuntimeError('Desktop Buddy is already using this data directory. Close the other instance first.')
        self.redactor = redactor
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root/'state.sqlite3',check_same_thread=False)
        os.chmod(self.root/'state.sqlite3',0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,conversation TEXT,body TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,conversation TEXT,status TEXT,checkpoint TEXT,updated REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,task TEXT,event TEXT,body TEXT,created REAL);
        ''')
        self.db.execute("UPDATE tasks SET status='interrupted' WHERE status IN ('running','waiting')")
        self.db.commit()
        # Version 1 stored "not checked" as False. It must not mean unsupported.
        if self.get('capability_schema', 1) < 2:
            with self.lock, self.db:
                for key in ('providers', 'model_profiles'):
                    values = self.get(key, {})
                    for config in values.values():
                        for capability in ('tools','vision'):
                            if config.get(capability) is False:config[capability] = None
                    self.set(key, values)
                self.set('capability_schema', 2)

    def set(self,key,value):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(self.redactor.clean(value))))

    def get(self,key,default=None):
        with self.lock:
            r=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default

    def configs(self):
        saved=self.get('providers',{})
        return {k:ProviderConfig(**saved[k]) if k in saved else ProviderConfig(k,v[1],v[2],v[3]) for k,v in PROVIDERS.items()}

    def save_config(self,config):
        config.validate()
        with self.lock:
            configs=self.get('providers',{})
            configs[config.id]=asdict(config)
            self.set('providers',configs)
            if config.model:
                profiles=self.get('model_profiles',{})
                profiles[config.id+':'+config.model]=asdict(config)
                self.set('model_profiles',profiles)

    def conversation(self,title='New conversation'):
        cid=uuid.uuid4().hex
        with self.lock,self.db:
            self.db.execute('INSERT INTO conversations VALUES (?,?,?)',(cid,self.redactor.text(title)[:100],time.time()))
        return cid

    def conversations(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('''
                SELECT c.*, COALESCE(MAX(m.created),c.created) AS updated
                FROM conversations c LEFT JOIN messages m ON m.conversation=c.id
                GROUP BY c.id ORDER BY updated DESC
            ''')]

    def rename_conversation(self, cid, title):
        title = self.redactor.text(' '.join(title.split()))[:100]
        if not title:
            raise ValueError('Give the conversation a name.')
        with self.lock, self.db:
            result = self.db.execute('UPDATE conversations SET title=? WHERE id=?', (title, cid))
            if not result.rowcount:
                raise ValueError('Conversation no longer exists.')

    def delete_conversation(self, cid):
        """Remove a transcript and its private, exclusively referenced image artifacts."""
        with self.lock:
            image_paths=set()
            for row in self.db.execute('SELECT body FROM messages WHERE conversation=?',(cid,)):
                try:
                    body=json.loads(row[0])
                    image_paths.update(x for x in body.get('images',[]) if isinstance(x,str))
                except (ValueError,TypeError):pass
            with self.db:
                if self.db.execute("SELECT 1 FROM tasks WHERE conversation=? AND status IN ('running','waiting')", (cid,)).fetchone():
                    raise ValueError('Stop the running task before deleting this conversation.')
                self.db.execute('DELETE FROM audit WHERE task IN (SELECT id FROM tasks WHERE conversation=?)', (cid,))
                self.db.execute('DELETE FROM tasks WHERE conversation=?', (cid,))
                self.db.execute('DELETE FROM messages WHERE conversation=?', (cid,))
                self.db.execute('DELETE FROM conversations WHERE id=?', (cid,))
                drafts=self.get('drafts',{});drafts.pop(cid,None);self.set('drafts',drafts)
            # Secure-delete overwrites freed cells; truncate the WAL so deleted
            # transcript pages are not retained in the active profile files.
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            remaining=set()
            for row in self.db.execute('SELECT body FROM messages'):
                try:
                    body=json.loads(row[0]);remaining.update(x for x in body.get('images',[]) if isinstance(x,str))
                except (ValueError,TypeError):pass
            remaining_resolved=set()
            for raw in remaining:
                try:remaining_resolved.add(Path(raw).resolve())
                except OSError:pass
            deleted=[];allowed=[(self.root/'images').resolve(),(self.root/'screenshots').resolve()]
            for raw in image_paths:
                path=Path(raw)
                try:resolved=path.resolve(strict=True)
                except OSError:continue
                if resolved in remaining_resolved or path.is_symlink() or not resolved.is_file():continue
                if not any(resolved.is_relative_to(folder) for folder in allowed):continue
                try:resolved.unlink();deleted.append(str(resolved))
                except OSError:pass
            return {'deleted_images':deleted}

    def add_message(self,cid,message):
        # Images remain local artifacts; never put base64 pixels in the database.
        message=self.redactor.clean(message)
        with self.lock,self.db:
            self.db.execute('INSERT INTO messages(conversation,body,created) VALUES (?,?,?)',(cid,json.dumps(message),time.time()))

    def messages(self,cid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM messages WHERE conversation=? ORDER BY id',(cid,))]

    def task(self,cid,checkpoint):
        tid=uuid.uuid4().hex
        self.checkpoint(tid,cid,'running',checkpoint)
        return tid

    def checkpoint(self,tid,cid,status,data):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?)',(tid,cid,status,json.dumps(self.redactor.clean(data)),time.time()))

    def tasks(self,cid):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM tasks WHERE conversation=? ORDER BY updated DESC',(cid,))]

    def audit(self,tid,event,data):
        with self.lock,self.db:
            self.db.execute('INSERT INTO audit(task,event,body,created) VALUES (?,?,?,?)',(tid,event,json.dumps(self.redactor.clean(data)),time.time()))

    def audits(self,tid=None):
        with self.lock:
            rows=self.db.execute('SELECT * FROM audit WHERE task=? ORDER BY id',(tid,)) if tid else self.db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')
            return [dict(r) for r in rows]

    def memory(self):
        p=self.root/'memory.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def save_memory(self,value):
        if not isinstance(value,dict):
            raise ValueError('Memory must be a JSON object.')
        p=self.root/'memory.json'
        tmp=p.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.redactor.clean(value),indent=2,ensure_ascii=False))
        os.chmod(tmp,0o600)
        tmp.replace(p)

    def close(self):
        with self.lock:
            self.db.close()
            self.lock_file.close()

class Credentials:
    """Secret Service only. No plaintext-file or environment fallback."""
    def __init__(self,redactor):
        self.redactor=redactor
        self.cache={}

    def _service(self):
        import gi
        gi.require_version('Secret','1')
        from gi.repository import Secret
        schema=Secret.Schema.new('io.desktopbuddy.Credentials',Secret.SchemaFlags.NONE,{
            'provider':Secret.SchemaAttributeType.STRING})
        return Secret,schema

    def get(self,provider):
        if provider not in self.cache:
            Secret,schema=self._service()
            value=Secret.password_lookup_sync(schema,{'provider':provider},None)
            self.cache[provider]=value or ''
            self.redactor.register(value)
        return self.cache[provider]

    def set(self,provider,value):
        Secret,schema=self._service()
        if value:
            ok=Secret.password_store_sync(schema,{'provider':provider},Secret.COLLECTION_DEFAULT,
                f'Desktop Buddy — {provider}',value,None)
        else:
            ok=Secret.password_clear_sync(schema,{'provider':provider},None)
        if value and not ok:
            raise RuntimeError('GNOME Keyring did not save the credential. No plaintext fallback was used.')
        self.cache[provider]=value
        self.redactor.register(value)
