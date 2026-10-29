"""Buddy changing its own code: the checkout it runs from, tested, committed and pushed.

The person turns this on once under Settings → IT setup ("Let Buddy change its
own code"). Everything else stays host-enforced: without the setting the package
directory is off limits to every model tool; with it, git internals and the
publisher's Google client file stay unreadable and uncommittable, a commit needs
the test run to have passed on exactly the files being committed, and every
commit must raise the version so the person can see which build they run.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import sys
import time
from . import __version__
from .paths import source_root
from .security import Denied, InvalidArguments

SETTING='self_update'
ENABLE_HINT='Settings → IT setup → Let Buddy change its own code'
NEVER_COMMIT=('publisher_local.py','.env')  # Credentials that must never leave this computer.
NEVER_SUFFIXES=('.pem','.key')
CODE_DIRS=('desktop_buddy','tests','scripts')
BRANCH='main'  # Buddy works on main: no feature branches to merge, no history rewriting.
DOCS_SUFFIXES=('.md','.txt','.png','.svg','.jpg','.jpeg','.gif','.webp')


def docs_only(paths):
    """True when every changed file is documentation or an image (README, docs/, assets/).
    Such a change needs no test run and no new version number."""
    def is_doc(name):
        parts=Path(name).parts
        if parts[0] in ('docs','assets') or name=='LICENSE':return True
        return parts[0] not in ('desktop_buddy','tests','scripts') and name.endswith(DOCS_SUFFIXES)
    return bool(paths) and all(is_doc(p) for p in paths)
DEFAULT_CHECK_SECONDS=900
VERSION=re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")
PYPROJECT_VERSION=re.compile(r"""^version\s*=\s*['"]([^'"]+)['"]""",re.M)


def version_in(text):
    match=VERSION.search(text or '')
    return match[1] if match else ''


def code_fingerprint(root):
    """Hash of every Python file Buddy could be running from, so a restart can be
    advised only when the files on disk differ from what this process loaded."""
    digest=hashlib.sha256()
    package=Path(root)/'desktop_buddy'
    for path in sorted(package.rglob('*.py')):
        if path.name in NEVER_COMMIT:continue
        digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes())
    return digest.hexdigest()


_LOADED=code_fingerprint(source_root()) if source_root() else ''


def restart_needed():
    """True when Buddy's files on disk differ from what this process loaded."""
    root=source_root()
    if not root:return False
    try:return code_fingerprint(root)!=_LOADED
    except OSError:return False


class SelfDev:
    """Tool handlers. Every method receives the task's StopToken as ``token``."""

    def __init__(self,store,policy,root=None,python=None,assistant=lambda:None):
        self.store=store;self.policy=policy
        self.root=Path(root).resolve() if root else source_root()
        self.python=python or sys.executable
        self.assistant=assistant
        self.checked=None  # {'fingerprint','passed','at','summary'} from the last self.check.
        self.restart_requested=False
        self.loaded_fingerprint=_LOADED if root is None else code_fingerprint(self.root)

    # ----- gates -----------------------------------------------------------
    @property
    def enabled(self):
        return bool(self.store.get(SETTING,False))

    def require(self):
        if self.root is None:
            raise Denied('Buddy is running from an installed copy, not from its source folder, so it cannot change its own code here.')
        if not self.enabled:
            raise Denied('Changing Buddy’s own code is turned off. The person can turn it on under '+ENABLE_HINT+'. Do not edit Buddy’s files or run git another way.')
        if not (self.root/'.git').exists():
            raise Denied('Buddy’s source folder is not a git checkout, so changes cannot be committed.')

    # ----- git -------------------------------------------------------------
    def git(self,args,token,timeout=120,network=False,check=True,raw=False):
        from .tools import run_process
        env={'GIT_TERMINAL_PROMPT':'0'}
        if network:
            # Commands run with a minimal environment; pushing needs the session's
            # SSH agent (GNOME keyring) so the person's own key signs the push.
            sock=os.environ.get('SSH_AUTH_SOCK') or str(Path(os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid())))/'gcr'/'ssh')
            if Path(sock).exists():env['SSH_AUTH_SOCK']=sock
        result=run_process(['git',*args],self.root,token,timeout,local_only=self.policy.local_only and not network,extra_env=env)
        if raw:return result
        if check and result['exit_code']!=0:
            raise RuntimeError('git '+' '.join(args[:2])+' failed: '+result['output'].strip()[-1500:])
        return result['output']

    def tree_fingerprint(self,token):
        """Identifies the exact uncommitted state: HEAD, the diff against it, and
        the contents of untracked (non-ignored) files."""
        digest=hashlib.sha256()
        digest.update(self.git(['rev-parse','HEAD'],token).encode())
        digest.update(self.git(['diff','HEAD','--binary'],token).encode())
        for name in self.git(['ls-files','--others','--exclude-standard'],token).split('\n'):
            if not name:continue
            path=self.root/name
            if path.is_file():digest.update(name.encode()+b'\0'+path.read_bytes())
        return digest.hexdigest()

    def changed_files(self,token):
        lines=[line for line in self.git(['status','--porcelain'],token).split('\n') if line.strip()]
        return [{'state':line[:2].strip() or '??','path':line[3:]} for line in lines]

    def branch(self,token):
        return self.git(['rev-parse','--abbrev-ref','HEAD'],token).strip()

    def require_main(self,token):
        branch=self.branch(token)
        if branch!=BRANCH:
            raise ValueError(f'Buddy works on the {BRANCH} branch, but the checkout is on {branch!r}. Ask the person to switch the checkout back to {BRANCH} first; Buddy does not switch branches itself.')
        return branch

    def head_version(self,token):
        return version_in(self.git(['show','HEAD:desktop_buddy/__init__.py'],token,check=False))

    def source_version(self):
        if self.root is None:return ''
        try:return version_in((self.root/'desktop_buddy'/'__init__.py').read_text())
        except OSError:return ''

    def pyproject_version(self):
        try:
            match=PYPROJECT_VERSION.search((self.root/'pyproject.toml').read_text())
            return match[1] if match else ''
        except OSError:return ''

    def restart_needed(self):
        if self.root is None:return False
        try:return code_fingerprint(self.root)!=self.loaded_fingerprint
        except OSError:return False

    # ----- tools -----------------------------------------------------------
    def status(self,x,token):
        if self.root is None:
            return {'enabled':False,'available':False,'reason':'Buddy runs from an installed copy, not a source checkout.'}
        result={'enabled':self.enabled,'available':True,'checkout':str(self.root),
                'running_version':__version__,'source_version':self.source_version(),
                'pyproject_version':self.pyproject_version(),'restart_needed':self.restart_needed(),
                'how_to_enable':None if self.enabled else ENABLE_HINT,
                'rules':'Work on the main branch. Read files with fs.read and change them with fs.write inside the checkout (README.md and docs/ included). '
                        'For code changes raise __version__ in desktop_buddy/__init__.py and version in pyproject.toml to the same new number and make self.check pass on the exact files you commit; '
                        'documentation-only changes (README.md, docs/, assets/) need neither. Then self.commit, self.push (which first brings in newer commits from GitHub), and for code changes self.restart. '
                        'Git internals and publisher_local.py are never readable or committable.'}
        if not self.enabled or not (self.root/'.git').exists():return result
        try:
            result['branch']=self.git(['rev-parse','--abbrev-ref','HEAD'],token).strip()
            result['remote']=self.git(['remote','get-url','origin'],token,check=False).strip()
            result['changed_files']=self.changed_files(token)
            result['recent_commits']=[line for line in self.git(['log','--oneline','-5'],token).split('\n') if line]
            counts=self.git(['rev-list','--left-right','--count','HEAD...@{upstream}'],token,check=False).split()
            if len(counts)==2:
                result['commits_ahead'],result['commits_behind']=int(counts[0]),int(counts[1])
                result['remote_counts_note']='As of the last fetch; self.push checks GitHub for newer commits itself.'
        except RuntimeError as error:result['git_error']=str(error)
        result['last_check']=self.checked and {k:v for k,v in self.checked.items() if k!='fingerprint'}
        result['check_is_current']=bool(self.checked and self.checked['passed'] and self.checked['fingerprint']==self.tree_fingerprint(token))
        return result

    def diff(self,x,token):
        self.require()
        text=self.git(['diff','HEAD','--stat'],token)+'\n'+self.git(['diff','HEAD'],token)
        untracked=[name for name in self.git(['ls-files','--others','--exclude-standard'],token).split('\n') if name]
        return {'diff':text[:60000],'truncated':len(text)>60000,'new_files':untracked}

    def compile_all(self):
        """Syntax-check every Python file before running or restarting on it."""
        problems=[]
        for folder in CODE_DIRS:
            for path in sorted((self.root/folder).rglob('*.py')):
                try:compile(path.read_bytes(),str(path),'exec')
                except (SyntaxError,ValueError) as error:
                    problems.append(f'{path.relative_to(self.root)}:{getattr(error,"lineno","?")}: {error.msg if isinstance(error,SyntaxError) else error}')
        return problems

    def check(self,x,token):
        self.require()
        modules=x.get('modules') or []
        if not isinstance(modules,list) or any(not isinstance(m,str) or not re.fullmatch(r'test_[A-Za-z0-9_]+',m) for m in modules):
            raise InvalidArguments('modules must be test module names such as test_core (no path or .py). Omit it to run every test.')
        timeout=x.get('timeout',DEFAULT_CHECK_SECONDS)
        if isinstance(timeout,bool) or not isinstance(timeout,int) or not 10<=timeout<=3600:
            raise InvalidArguments('timeout is in seconds, between 10 and 3600.')
        fingerprint=self.tree_fingerprint(token)
        problems=self.compile_all()
        if problems:
            self.checked={'fingerprint':fingerprint,'passed':False,'at':time.time(),'summary':'Syntax errors.'}
            return {'passed':False,'stage':'compile','problems':problems,'next':'Fix these files, then run self.check again.'}
        from .tools import run_process
        argv=[self.python,'-m','unittest','discover','-s','tests','-p',(modules[0] if len(modules)==1 else 'test_*')+'.py']
        if len(modules)>1:argv=[self.python,'-m','unittest','-v',*modules]
        result=run_process(argv,self.root,token,timeout,local_only=self.policy.local_only)
        output=result['output'];passed=result['exit_code']==0
        ran=re.findall(r'Ran (\d+) tests?',output)
        failures=re.findall(r'^(?:FAIL|ERROR): (.*)$',output,re.M)
        summary=f'{"Passed" if passed else "Failed"}: {ran[-1] if ran else "?"} tests, {len(failures)} failing.'
        self.checked={'fingerprint':fingerprint,'passed':passed,'at':time.time(),'summary':summary,'modules':modules}
        return {'passed':passed,'stage':'tests','tests_run':int(ran[-1]) if ran else None,'failing':failures[:40],
                'output_tail':output[-6000:],'elapsed_seconds':result['elapsed_seconds'],
                'next':'Commit with self.commit.' if passed else 'Read the failing tests, fix the code (or the test if the behaviour changed on purpose), then run self.check again.'}

    def commit(self,x,token):
        self.require()
        message=x.get('message','')
        if not isinstance(message,str) or not message.strip():raise InvalidArguments('message is required: one short line saying what changed for the person, optionally followed by details.')
        message=message.strip()
        subject=message.split('\n',1)[0]
        if len(subject)>72:raise InvalidArguments('The first line of message must be at most 72 characters.')
        if len(message)>4000:raise InvalidArguments('message must be at most 4000 characters.')
        self.require_main(token)
        changed=self.changed_files(token)
        if not changed:raise ValueError('Nothing to commit: the checkout has no changes.')
        documentation=docs_only([c['path'] for c in changed])
        source=self.source_version()
        if not documentation:
            # Code changed: the tests must have passed on exactly these files and
            # the person must be able to tell the new build apart.
            fingerprint=self.tree_fingerprint(token)
            if not self.checked or self.checked['fingerprint']!=fingerprint:
                raise ValueError('Run self.check first: the tests have not been run on these exact changes.')
            if not self.checked['passed']:
                raise ValueError('The last self.check failed. Fix the code and run self.check again before committing.')
            head=self.head_version(token)
            if not source:raise ValueError('desktop_buddy/__init__.py has no __version__ line.')
            if source==head:
                raise ValueError(f'Raise the version first: desktop_buddy/__init__.py still says {source}, the same as the last commit. Use the next number (for example {suggest_next(source)}) in both desktop_buddy/__init__.py and pyproject.toml so the person can see which build they run.')
            if self.pyproject_version()!=source:
                raise ValueError(f'pyproject.toml says version {self.pyproject_version() or "nothing"} but desktop_buddy/__init__.py says {source}. Make them the same.')
        self.git(['add','-A'],token)
        staged=[name for name in self.git(['diff','--cached','--name-only'],token).split('\n') if name]
        blocked=[name for name in staged if Path(name).name in NEVER_COMMIT or name.endswith(NEVER_SUFFIXES)]
        if blocked:
            self.git(['reset','-q'],token)
            raise Denied('Refusing to commit files that hold credentials: '+', '.join(blocked)+'. Remove them from the checkout or add them to .gitignore.')
        if not staged:
            raise ValueError('Nothing to commit after ignoring excluded files.')
        config=self.assistant()
        trailer='Made-with: Desktop Buddy '+__version__+(f' ({config.id}: {config.model})' if config is not None and getattr(config,'model','') else '')
        self.git([*self.identity(token),'commit','-q','-m',message,'-m',trailer],token)
        sha=self.git(['rev-parse','HEAD'],token).strip()
        recorded=self.git(['log','-1','--format=%s'],token).strip()
        return {'commit':sha[:12],'subject':recorded,'files':staged,'version':source,'verified':recorded==subject,
                'documentation_only':documentation,
                'next':'self.push publishes it.'+('' if documentation else ' self.restart makes Buddy run it.')}

    def identity(self,token):
        """Commits are made in the person's name. When git has no identity on this
        computer, reuse the author of the checkout's latest commit."""
        name=self.git(['config','user.name'],token,check=False).strip()
        email=self.git(['config','user.email'],token,check=False).strip()
        if name and email:return []
        last=self.git(['log','-1','--format=%an%n%ae'],token,check=False).split('\n')
        name=name or (last[0].strip() if last else '');email=email or (last[1].strip() if len(last)>1 else '')
        if not (name and email):
            raise ValueError('Git does not know whose name to put on the commit. Ask the person to run git config user.name and user.email in the checkout once.')
        return ['-c','user.name='+name,'-c','user.email='+email]

    def push(self,x,token):
        self.require()
        branch=self.require_main(token)
        remote=self.git(['remote','get-url','origin'],token,check=False).strip()
        if not remote:raise ValueError('The checkout has no remote named origin.')
        # Work off main: bring in anything newer on GitHub first, by rebasing
        # Buddy's commits on top. A conflict is left for the person, never forced.
        fetched=self.git(['fetch','-q','origin',branch],token,timeout=120,network=True,raw=True)
        if fetched['exit_code']!=0:
            raise ValueError('Could not reach GitHub to check for newer commits: '+self.redact(fetched['output']).strip()[-800:])
        counts=self.git(['rev-list','--left-right','--count',f'HEAD...origin/{branch}'],token).split()
        ahead,behind=int(counts[0]),int(counts[1])
        rebased=False
        if behind:
            if self.changed_files(token):
                raise ValueError(f'GitHub has {behind} newer commit(s) on {branch} and the checkout still has uncommitted changes. Commit them first (self.commit), then push again.')
            pulled=self.git([*self.identity(token),'pull','--rebase','-q','origin',branch],token,timeout=120,network=True,raw=True)
            if pulled['exit_code']!=0:
                self.git(['rebase','--abort'],token,check=False)
                raise ValueError('GitHub has newer commits that could not be combined with Buddy’s automatically. Tell the person a manual merge is needed; Buddy does not force-push. Details: '+self.redact(pulled['output']).strip()[-800:])
            rebased=True
            if ahead and self.compile_all():
                raise ValueError('After combining with the newer commits from GitHub the code no longer compiles. Fix it, run self.check, commit and push again.')
        result=self.git(['push','origin',branch],token,timeout=300,network=True,raw=True)
        output=self.redact(result['output'])
        if result['exit_code']!=0:
            raise ValueError('Push did not succeed: '+output.strip()[-1500:]+'\nDo not force-push or rewrite history; tell the person what GitHub answered.')
        counts=self.git(['rev-list','--left-right','--count',f'HEAD...origin/{branch}'],token,check=False).split()
        left=int(counts[0]) if len(counts)==2 else None
        return {'pushed':left==0,'branch':branch,'remote':remote,'commits_pushed':ahead,'combined_with_newer_commits':rebased,
                'output':output.strip()[-2000:],'uncommitted_changes':[c['path'] for c in self.changed_files(token)],
                'next':('Newer commits from GitHub were combined in; run self.check once more before self.restart.' if rebased and ahead else '')}

    def restart(self,x,token):
        self.require()
        problems=self.compile_all()
        if problems:raise ValueError('Buddy will not restart on code that does not compile:\n'+'\n'.join(problems[:5]))
        self.restart_requested=True
        return {'scheduled':True,'restart_needed':self.restart_needed(),
                'note':'Buddy restarts itself as soon as this task finishes, after you give your final reply. Conversations are kept. If other conversations are still working, the person sees a Restart Buddy button instead.'}

    @staticmethod
    def redact(text):
        return re.sub(r'https?://[^@\s]+@','https://[REDACTED]@',text)


def suggest_next(version):
    parts=version.split('.')
    try:parts[-1]=str(int(parts[-1])+1);return '.'.join(parts)
    except ValueError:return version+'.1'
