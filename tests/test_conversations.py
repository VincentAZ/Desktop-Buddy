import tempfile
import unittest
from pathlib import Path

from desktop_buddy.security import Redactor
from desktop_buddy.state import Store


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name, Redactor())

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_delete_removes_related_records_and_only_exclusive_private_images(self):
        deleted = self.store.conversation('Delete me')
        kept = self.store.conversation('Keep me')
        for cid in (deleted, kept):
            self.store.add_message(cid, {'role': 'user', 'content': 'hello'})
            tid = self.store.task(cid, {'step': 1})
            self.store.audit(tid, 'tool', {'name': 'fs.read'})
            self.store.checkpoint(tid, cid, 'complete', {})
        self.store.save_memory({'preference': 'local'})
        self.store.set('provider', 'ollama')
        artifact = Path(self.directory.name) / 'observation.png'
        artifact.write_bytes(b'owned screenshot stand-in')
        images=Path(self.directory.name)/'images';images.mkdir()
        exclusive=images/'exclusive.png';exclusive.write_bytes(b'exclusive')
        shared=images/'shared.png';shared.write_bytes(b'shared')
        self.store.add_message(deleted,{'role':'user','content':'visuals','images':[str(exclusive),str(shared),str(artifact)]})
        self.store.add_message(kept,{'role':'user','content':'shared visual','images':[str(shared)]})
        result=self.store.delete_conversation(deleted)
        self.assertEqual([r['id'] for r in self.store.conversations()], [kept])
        self.assertEqual(self.store.messages(deleted), [])
        self.assertEqual(self.store.tasks(deleted), [])
        self.assertEqual(len(self.store.audits()), 1)
        self.assertEqual(len(self.store.messages(kept)), 2)
        self.assertEqual(self.store.memory(), {'preference': 'local'})
        self.assertEqual(self.store.get('provider'), 'ollama')
        self.assertTrue(artifact.exists())
        self.assertFalse(exclusive.exists());self.assertTrue(shared.exists())
        self.assertEqual(result['deleted_images'],[str(exclusive)])

    def test_running_task_blocks_deletion_without_partial_removal(self):
        cid = self.store.conversation()
        self.store.add_message(cid, {'role': 'user', 'content': 'hello'})
        tid = self.store.task(cid, {})
        self.store.audit(tid, 'start', {})
        with self.assertRaisesRegex(ValueError, 'Stop'):
            self.store.delete_conversation(cid)
        self.assertEqual(len(self.store.messages(cid)), 1)
        self.assertEqual(len(self.store.tasks(cid)), 1)
        self.assertEqual(len(self.store.audits()), 1)

    def test_rename_validates_and_redacts(self):
        cid = self.store.conversation()
        self.store.redactor.register('secret-to-redact')
        self.store.rename_conversation(cid, '  secret-to-redact project  ')
        self.assertNotIn('secret-to-redact', self.store.conversations()[0]['title'])
        with self.assertRaises(ValueError):self.store.rename_conversation(cid, '  ')
        with self.assertRaises(ValueError):self.store.rename_conversation('missing', 'name')

    def test_delete_removes_transcript_bytes_from_database_and_wal(self):
        marker = 'unique-deleted-transcript-marker-791fe2'
        cid = self.store.conversation(marker)
        self.store.add_message(cid, {'role': 'user', 'content': marker})
        self.store.delete_conversation(cid)
        files = [Path(self.directory.name) / name for name in ('state.sqlite3', 'state.sqlite3-wal')]
        remaining = b''.join(path.read_bytes() for path in files if path.exists())
        self.assertNotIn(marker.encode(), remaining)
