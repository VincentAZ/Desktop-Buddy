"""Buddy changing its own code: host-gated, tested before commit, versioned, pushed."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from desktop_buddy.security import Denied, Policy, Redactor, StopToken
from desktop_buddy.selfdev import SETTING, SelfDev, code_fingerprint, docs_only, suggest_next
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry

INIT='"""fixture"""\n__version__ = "0.1.0"\n'
PASSING='import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):self.assertTrue(True)\n'
FAILING='import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):self.fail("broken")\n'


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True,
                          env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}).stdout


class Checkout(unittest.TestCase):
    """A miniature Desktop Buddy checkout with its own bare remote."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.tmproot=Path(self.tmp.name)
        self.root=self.tmproot/'checkout';self.root.mkdir()
        (self.root/'desktop_buddy').mkdir();(self.root/'tests').mkdir();(self.root/'scripts').mkdir()
        (self.root/'desktop_buddy'/'__init__.py').write_text(INIT)
        (self.root/'pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "0.1.0"\n')
        (self.root/'launch.sh').write_text('#!/bin/sh\n')
        (self.root/'tests'/'test_fixture.py').write_text(PASSING)
        (self.root/'.gitignore').write_text('__pycache__/\n.buddy/\n')
        git(self.root,'init','-q','-b','main');git(self.root,'config','user.name','Fixture');git(self.root,'config','user.email','fixture@example.invalid')
        git(self.root,'add','-A');git(self.root,'commit','-q','-m','Initial')
        self.remote=self.tmproot/'remote.git';git(self.tmproot,'init','-q','--bare','-b','main',str(self.remote))
        git(self.root,'remote','add','origin',str(self.remote));git(self.root,'push','-q','-u','origin','main')
        self.workspace=self.tmproot/'work';self.workspace.mkdir()
        self.store=Store(self.tmproot/'state',Redactor());self.token=StopToken()
        self.store.set(SETTING,True)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def dev(self,mode='yolo',local=False):
        policy=Policy(self.workspace,mode,local,lambda *_:True,protected=[self.store.root],source_root=self.root)
        return SelfDev(self.store,policy,root=self.root,python=sys.executable)

    def bump(self,version='0.1.1'):
        (self.root/'desktop_buddy'/'__init__.py').write_text(INIT.replace('0.1.0',version))
        (self.root/'pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "'+version+'"\n')


class SelfDevTests(Checkout):
    def test_setting_off_blocks_every_change_but_status_explains(self):
        self.store.set(SETTING,False);dev=self.dev()
        status=dev.status({},self.token)
        self.assertFalse(status['enabled']);self.assertIn('IT setup',status['how_to_enable'])
        for method in (dev.diff,dev.check,dev.commit,dev.push,dev.restart):
            with self.subTest(method=method.__name__),self.assertRaises(Denied):method({'message':'x'},self.token)

    def test_commit_needs_changes_a_current_passing_check_and_a_raised_version(self):
        dev=self.dev()
        with self.assertRaisesRegex(ValueError,'Nothing to commit'):dev.commit({'message':'Nothing'},self.token)
        (self.root/'desktop_buddy'/'feature.py').write_text('VALUE=1\n')
        with self.assertRaisesRegex(ValueError,'self.check first'):dev.commit({'message':'Add feature'},self.token)
        check=dev.check({},self.token)
        self.assertTrue(check['passed']);self.assertEqual(check['tests_run'],1)
        with self.assertRaisesRegex(ValueError,'Raise the version'):dev.commit({'message':'Add feature'},self.token)
        self.bump()
        # The version bump changed the files, so the earlier check no longer counts.
        with self.assertRaisesRegex(ValueError,'self.check first'):dev.commit({'message':'Add feature'},self.token)
        self.assertTrue(dev.check({},self.token)['passed'])
        result=dev.commit({'message':'Add feature\n\nDetails for the log.'},self.token)
        self.assertTrue(result['verified']);self.assertEqual(result['version'],'0.1.1')
        self.assertIn('desktop_buddy/feature.py',result['files'])
        body=git(self.root,'log','-1','--format=%B')
        self.assertIn('Made-with: Desktop Buddy',body);self.assertIn('Details for the log.',body)
        self.assertEqual(dev.status({},self.token)['changed_files'],[])

    def test_mismatched_pyproject_version_is_refused(self):
        dev=self.dev()
        (self.root/'desktop_buddy'/'__init__.py').write_text(INIT.replace('0.1.0','0.1.1'))
        dev.check({},self.token)
        with self.assertRaisesRegex(ValueError,'pyproject.toml'):dev.commit({'message':'Bump'},self.token)

    def test_failing_tests_block_the_commit_and_are_reported(self):
        dev=self.dev()
        (self.root/'tests'/'test_fixture.py').write_text(FAILING);self.bump()
        check=dev.check({},self.token)
        self.assertFalse(check['passed']);self.assertEqual(len(check['failing']),1);self.assertIn('broken',check['output_tail'])
        with self.assertRaisesRegex(ValueError,'last self.check failed'):dev.commit({'message':'Broken'},self.token)
        self.assertEqual(git(self.root,'log','--oneline').count('\n'),1)

    def test_syntax_errors_are_caught_before_tests_and_block_restart(self):
        dev=self.dev()
        (self.root/'desktop_buddy'/'bad.py').write_text('def (:\n')
        check=dev.check({},self.token)
        self.assertFalse(check['passed']);self.assertEqual(check['stage'],'compile');self.assertIn('bad.py',check['problems'][0])
        with self.assertRaisesRegex(ValueError,'does not compile'):dev.restart({},self.token)
        self.assertFalse(dev.restart_requested)

    def test_credential_files_are_never_committed(self):
        dev=self.dev()
        (self.root/'desktop_buddy'/'publisher_local.py').write_text('CLIENT_SECRET="x"\n');self.bump()
        dev.check({},self.token)
        with self.assertRaises(Denied):dev.commit({'message':'Leak'},self.token)
        self.assertEqual(git(self.root,'diff','--cached','--name-only'),'')
        self.assertEqual(git(self.root,'log','--oneline').count('\n'),1)

    def test_push_publishes_the_commit_with_the_session_agent(self):
        dev=self.dev()
        (self.root/'desktop_buddy'/'feature.py').write_text('VALUE=2\n');self.bump()
        dev.check({},self.token);dev.commit({'message':'Add feature'},self.token)
        result=dev.push({},self.token)
        self.assertTrue(result['pushed']);self.assertEqual(result['branch'],'main')
        self.assertEqual(git(self.tmproot,'--git-dir',str(self.remote),'log','-1','--format=%s').strip(),'Add feature')

    def test_missing_git_identity_falls_back_to_the_last_author(self):
        git(self.root,'config','--unset','user.name');git(self.root,'config','--unset','user.email')
        dev=self.dev()
        (self.root/'desktop_buddy'/'feature.py').write_text('VALUE=4\n');self.bump()
        dev.check({},self.token);dev.commit({'message':'Keep the author'},self.token)
        self.assertEqual(git(self.root,'log','-1','--format=%an <%ae>').strip(),'Fixture <fixture@example.invalid>')

    def test_offline_mode_blocks_the_push_but_not_the_local_work(self):
        registry=Registry(Policy(self.workspace,'yolo',True,lambda *_:True,protected=[self.store.root],source_root=self.root),self.store)
        registry.selfdev=self.dev(local=True)
        with self.assertRaises(Denied):registry.execute('self.push',{},self.token)

    def test_restart_is_scheduled_only_through_the_tool_and_fingerprint_tracks_disk(self):
        dev=self.dev()
        before=code_fingerprint(self.root)
        self.assertFalse(dev.restart_needed())
        (self.root/'desktop_buddy'/'feature.py').write_text('VALUE=3\n')
        self.assertNotEqual(code_fingerprint(self.root),before);self.assertTrue(dev.restart_needed())
        self.assertTrue(dev.restart({},self.token)['scheduled']);self.assertTrue(dev.restart_requested)

    def test_invalid_arguments_perform_nothing(self):
        dev=self.dev()
        for args in ({'message':''},{'message':'x'*80},{'message':5}):
            with self.subTest(args=args),self.assertRaises(ValueError):dev.commit(args,self.token)
        for args in ({'modules':['../evil']},{'modules':'test_x'},{'timeout':5}):
            with self.subTest(args=args),self.assertRaises(ValueError):dev.check(args,self.token)
        self.assertIsNone(dev.checked)

    def test_readme_and_docs_commit_without_tests_or_a_version_bump(self):
        dev=self.dev()
        (self.root/'README.md').write_text('# Fixture\n\nUpdated by Buddy.\n');(self.root/'docs').mkdir();(self.root/'docs'/'GUIDE.md').write_text('Guide\n')
        result=dev.commit({'message':'Update the README'},self.token)
        self.assertTrue(result['documentation_only']);self.assertEqual(sorted(result['files']),['README.md','docs/GUIDE.md'])
        self.assertEqual(result['version'],'0.1.0')
        self.assertTrue(dev.push({},self.token)['pushed'])
        self.assertEqual(git(self.tmproot,'--git-dir',str(self.remote),'log','-1','--format=%s').strip(),'Update the README')
        # Mixing a code edit back in restores the code rules.
        (self.root/'README.md').write_text('# Fixture again\n');(self.root/'desktop_buddy'/'feature.py').write_text('VALUE=9\n')
        with self.assertRaisesRegex(ValueError,'self.check first'):dev.commit({'message':'Mixed'},self.token)
        self.assertTrue(docs_only(['README.md','docs/a/b.md','assets/logo.svg','LICENSE']))
        self.assertFalse(docs_only(['README.md','desktop_buddy/skills/files.md']));self.assertFalse(docs_only([]))

    def test_buddy_only_works_on_main(self):
        dev=self.dev()
        git(self.root,'checkout','-q','-b','experiment')
        (self.root/'README.md').write_text('branch\n')
        with self.assertRaisesRegex(ValueError,"on 'experiment'"):dev.commit({'message':'Branch'},self.token)
        with self.assertRaisesRegex(ValueError,"on 'experiment'"):dev.push({},self.token)
        self.assertEqual(git(self.root,'rev-parse','--abbrev-ref','HEAD').strip(),'experiment')

    def test_push_brings_in_newer_commits_from_the_remote_first(self):
        other=self.tmproot/'other';git(self.tmproot,'clone','-q',str(self.remote),str(other))
        git(other,'config','user.name','Other');git(other,'config','user.email','other@example.invalid')
        (other/'docs').mkdir();(other/'docs'/'NEW.md').write_text('from elsewhere\n');git(other,'add','-A');git(other,'commit','-q','-m','Remote change');git(other,'push','-q')
        git(self.root,'config','--unset','user.name');git(self.root,'config','--unset','user.email')  # a fresh clone has none
        dev=self.dev()
        (self.root/'README.md').write_text('local\n')
        dev.commit({'message':'Local change'},self.token)
        result=dev.push({},self.token)
        self.assertTrue(result['pushed']);self.assertTrue(result['combined_with_newer_commits']);self.assertEqual(result['commits_pushed'],1)
        log=git(self.tmproot,'--git-dir',str(self.remote),'log','--format=%s')
        self.assertEqual(log.strip().split('\n')[:2],['Local change','Remote change'])
        self.assertTrue((self.root/'docs'/'NEW.md').exists())

    def test_push_refuses_to_combine_while_changes_are_uncommitted_and_never_forces(self):
        other=self.tmproot/'other';git(self.tmproot,'clone','-q',str(self.remote),str(other))
        git(other,'config','user.name','Other');git(other,'config','user.email','other@example.invalid')
        (other/'README.md').write_text('remote wins\n');git(other,'add','-A');git(other,'commit','-q','-m','Remote README');git(other,'push','-q')
        dev=self.dev()
        (self.root/'README.md').write_text('local draft\n')
        with self.assertRaisesRegex(ValueError,'uncommitted'):dev.push({},self.token)
        dev.commit({'message':'Local README'},self.token)
        with self.assertRaisesRegex(ValueError,'manual merge'):dev.push({},self.token)
        self.assertFalse((self.root/'.git'/'rebase-merge').exists());self.assertFalse((self.root/'.git'/'rebase-apply').exists())
        self.assertEqual(git(self.tmproot,'--git-dir',str(self.remote),'log','-1','--format=%s').strip(),'Remote README')
        self.assertEqual(git(self.root,'log','-1','--format=%s').strip(),'Local README')

    def test_suggest_next_version(self):
        self.assertEqual(suggest_next('0.4.8'),'0.4.9');self.assertEqual(suggest_next('1.0'),'1.1');self.assertEqual(suggest_next('beta'),'beta.1')


class PolicyTests(Checkout):
    def test_checkout_is_protected_until_the_setting_opens_it_and_secrets_stay_closed(self):
        package=self.root/'desktop_buddy'
        closed=Policy(self.workspace,'yolo',False,lambda *_:True,protected=[self.store.root,package])
        with self.assertRaises(Denied):closed.path(package/'__init__.py')
        opened=Policy(self.workspace,'yolo',False,lambda *_:True,protected=[self.store.root],source_root=self.root)
        self.assertEqual(opened.path(package/'__init__.py'),package/'__init__.py')
        self.assertEqual(opened.path(self.root/'tests'/'test_fixture.py'),self.root/'tests'/'test_fixture.py')
        for blocked in (self.root/'.git'/'config',self.root/'.git'/'hooks'/'pre-commit',package/'publisher_local.py',self.store.root/'state.sqlite3'):
            with self.subTest(path=blocked),self.assertRaises(Denied):opened.path(blocked)

    def test_discuss_only_blocks_changes_and_ask_mode_confirms_them(self):
        plan=Policy(self.workspace,'plan',False,Mock(return_value=True),source_root=self.root)
        with self.assertRaises(Denied):plan.authorize('self.commit',{'message':'x'},True,'self_update',self.token)
        asked=Mock(return_value=False)
        execute=Policy(self.workspace,'execute',False,asked,source_root=self.root)
        with self.assertRaises(Denied):execute.authorize('self.check',{},True,'self_update',self.token)
        asked.assert_called_once()
        quiet=Mock(side_effect=AssertionError('Just do it must not prompt'))
        Policy(self.workspace,'yolo',False,quiet,source_root=self.root).authorize('self.commit',{'message':'x'},True,'self_update',self.token)

    def test_registry_registers_the_tools_with_their_risks(self):
        registry=Registry(Policy(self.workspace,'plan',True),self.store)
        risks={name:(tool.mutation,tool.risk) for name,tool in registry.tools.items() if name.startswith('self.')}
        self.assertEqual(risks,{'self.status':(False,'read'),'self.diff':(False,'read'),'self.check':(True,'self_update'),
                                'self.commit':(True,'self_update'),'self.push':(True,'network'),'self.restart':(True,'self_update')})
        self.assertTrue(any(t['name']=='self.commit' for t in registry.discover('commit push code')))


if __name__=='__main__':unittest.main()
