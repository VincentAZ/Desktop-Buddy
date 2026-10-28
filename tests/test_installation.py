import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('buddy_installer', Path(__file__).resolve().parents[1] / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def build(path):
    (path / 'bin').mkdir()
    (path / 'pyvenv.cfg').write_text('test environment')
    (path / 'bin/python').write_text('test executable')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='buddy install ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / 'app'
        self.desktop = self.root / 'applications/Buddy.desktop'

    def install(self, **kwargs):
        return installer.install(self.prefix, self.desktop, build, **kwargs)

    def test_upgrade_and_rollback_preserve_profile_and_old_environment(self):
        first = self.install()
        (self.prefix / 'state.db').write_bytes(b'conversation sentinel')
        original_launcher = self.desktop.read_bytes()
        second = self.install(upgrade=True)
        self.assertEqual(second['previous'], first['active'])
        self.assertNotEqual(first['active'], second['active'])
        self.assertEqual(self.desktop.read_bytes(), original_launcher)
        self.assertTrue((self.prefix / first['active'] / 'bin/python').is_file())
        restored = installer.rollback(self.prefix, verify=lambda path: None)
        self.assertEqual(restored['active'], first['active'])
        self.assertEqual((self.prefix / 'state.db').read_bytes(), b'conversation sentinel')
        self.assertIn(str(self.prefix / 'desktop-buddy'), self.desktop.read_text())

    def test_failed_build_leaves_old_version_selected(self):
        first = self.install()
        def broken(path):
            build(path)
            raise RuntimeError('dependency install failed')
        with self.assertRaises(RuntimeError):
            installer.install(self.prefix, self.desktop, broken, upgrade=True)
        self.assertEqual(installer.read_installation(self.prefix), first)
        self.assertEqual(len(list((self.prefix / 'releases').iterdir())), 1)

    def test_rejects_custom_launcher_and_concurrent_installation(self):
        self.install()
        self.desktop.write_text('user launcher')
        with self.assertRaisesRegex(ValueError, 'launcher has changed'):
            self.install(upgrade=True)
        self.assertEqual(self.desktop.read_text(), 'user launcher')
        with installer.installation_lock(self.prefix):
            with self.assertRaisesRegex(ValueError, 'Another installation'):
                self.install(upgrade=True)

    def test_legacy_update_keeps_old_environment_for_rollback(self):
        legacy = self.prefix / 'venv'
        legacy.mkdir(parents=True)
        build(legacy)
        self.desktop.parent.mkdir()
        self.desktop.write_text(installer.desktop_text(legacy / 'bin/desktop-buddy', legacy=True))
        info = self.install(upgrade=True)
        self.assertEqual(info['previous'], 'venv')
        restored = installer.rollback(self.prefix, verify=lambda path: None)
        self.assertEqual(restored['active'], 'venv')
        self.assertTrue((legacy / 'bin/python').exists())

    def test_failed_manifest_publication_restores_legacy_launcher(self):
        legacy = self.prefix / 'venv'
        legacy.mkdir(parents=True)
        build(legacy)
        self.desktop.parent.mkdir()
        original = installer.desktop_text(legacy / 'bin/desktop-buddy', legacy=True)
        self.desktop.write_text(original)
        publish = installer.publish
        def fail(path, text, **kwargs):
            if path.name == 'installation.json': raise OSError('disk full')
            return publish(path, text, **kwargs)
        with patch.object(installer, 'publish', side_effect=fail):
            with self.assertRaises(OSError): self.install(upgrade=True)
        self.assertEqual(self.desktop.read_text(), original)
        self.assertFalse((self.prefix / 'desktop-buddy').exists())
        self.assertEqual(list((self.prefix / 'releases').iterdir()), [])

    def test_error_after_commit_never_deletes_selected_code(self):
        first = self.install()
        publish = installer.publish
        def fail_after(path, text, **kwargs):
            publish(path, text, **kwargs)
            if path.name == 'installation.json': raise OSError('post-commit failure')
        with patch.object(installer, 'publish', side_effect=fail_after):
            with self.assertRaises(OSError): self.install(upgrade=True)
        selected = installer.read_installation(self.prefix)
        self.assertNotEqual(selected['active'], first['active'])
        self.assertTrue((self.prefix / selected['active'] / 'bin/python').is_file())

    def test_failed_first_install_does_not_advertise_launcher(self):
        publish = installer.publish
        def fail(path, text, **kwargs):
            if path.name == 'installation.json': raise OSError('disk full')
            publish(path, text, **kwargs)
        with patch.object(installer, 'publish', side_effect=fail):
            with self.assertRaises(OSError): self.install()
        self.assertFalse(self.desktop.exists())
        self.assertFalse((self.prefix / 'desktop-buddy').exists())
        self.install()  # A clean retry succeeds.

    def test_error_after_desktop_publication_retains_working_installation(self):
        publish = installer.publish
        def fail_after(path, text, **kwargs):
            publish(path, text, **kwargs)
            if path == self.desktop: raise OSError('interrupted after launcher write')
        with patch.object(installer, 'publish', side_effect=fail_after):
            with self.assertRaises(OSError): self.install()
        selected = installer.read_installation(self.prefix)
        self.assertTrue((self.prefix / selected['active'] / 'bin/python').exists())
        self.assertTrue(self.desktop.exists())
        self.install(upgrade=True)

    def test_interrupted_legacy_launcher_switch_keeps_old_entry_and_can_resume(self):
        legacy = self.prefix / 'venv'
        legacy.mkdir(parents=True)
        build(legacy)
        self.desktop.parent.mkdir()
        original = installer.desktop_text(legacy / 'bin/desktop-buddy', legacy=True)
        self.desktop.write_text(original)
        publish = installer.publish
        def fail(path, text, **kwargs):
            if path == self.desktop: raise OSError('interrupted before launcher switch')
            publish(path, text, **kwargs)
        with patch.object(installer, 'publish', side_effect=fail):
            with self.assertRaises(OSError): self.install(upgrade=True)
        self.assertEqual(self.desktop.read_text(), original)
        self.assertTrue((legacy / 'bin/python').exists())
        self.install(upgrade=True)
        self.assertEqual(self.desktop.read_text(), installer.desktop_text(self.prefix / 'desktop-buddy'))

    def test_first_install_can_recover_missing_desktop_entry(self):
        publish = installer.publish
        def fail(path, text, **kwargs):
            if path == self.desktop: raise OSError('desktop directory full')
            publish(path, text, **kwargs)
        with patch.object(installer, 'publish', side_effect=fail):
            with self.assertRaises(OSError): self.install()
        self.assertFalse(self.desktop.exists())
        installer.read_installation(self.prefix)
        self.install(upgrade=True)
        self.assertTrue(self.desktop.exists())

    def test_first_install_resumes_after_wrapper_only_interruption(self):
        self.prefix.mkdir()
        (self.prefix / 'desktop-buddy').write_text(installer.RUNNER)
        self.install()
        self.assertTrue(self.desktop.exists())

    def test_symlink_or_modified_wrapper_not_overwritten(self):
        self.install()
        runner = self.prefix / 'desktop-buddy'
        runner.write_text('custom wrapper')
        with self.assertRaisesRegex(ValueError, 'launcher has changed'):
            self.install(upgrade=True)
        self.assertEqual(runner.read_text(), 'custom wrapper')

    def test_desktop_entry_launches_literal_paths_through_gnome(self):
        import gi
        gi.require_version('GioUnix', '2.0')
        from gi.repository import GioUnix
        for part in ['normal path', 'percent%name', 'dollar$name', 'back`tick',
                     'quote"name', r'back\slash', "single'quote", 'equal=name']:
            with self.subTest(path=part):
                script = self.root / part
                script.write_text("from pathlib import Path; Path(__file__ + '.done').write_text('launched')")
                desktop = self.root / 'probe.desktop'
                desktop.write_text(installer.desktop_text(script))
                application = GioUnix.DesktopAppInfo.new_from_filename(str(desktop))
                self.assertTrue(application.launch([], None))
                marker = Path(str(script) + '.done')
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(marker.read_text(), 'launched')
        with self.assertRaises(ValueError): installer.desktop_quote('bad\npath')


if __name__ == '__main__': unittest.main()
