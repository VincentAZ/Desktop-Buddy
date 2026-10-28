"""Exercise native capture startup against an unresponsive private connection."""
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from desktop_buddy.desktop import Portal, DesktopError
from desktop_buddy.security import StopToken, Cancelled


class CaptureProcessTests(unittest.TestCase):
    def setUp(self):
        self.client,self.server=socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.close)
        self.children=[]
        real_popen=subprocess.Popen
        def launch(*args,**kwargs):
            child=real_popen(*args,**kwargs);self.children.append(child);return child
        patcher=patch('desktop_buddy.desktop.subprocess.Popen',side_effect=launch)
        patcher.start();self.addCleanup(patcher.stop)

    def assert_stopped(self):
        self.assertEqual(len(self.children),1)
        self.assertIsNotNone(self.children[0].poll())
        self.assertGreaterEqual(self.client.fileno(),0)  # Parent still owns its FD.

    def test_unresponsive_native_capture_has_a_hard_deadline(self):
        started=time.monotonic()
        with self.assertRaisesRegex(DesktopError,'screen check has stopped'):
            Portal._capture_frame_process(self.client.fileno(),1,StopToken(),timeout=.3)
        self.assertLess(time.monotonic()-started,3)
        self.assert_stopped()

    def test_stop_terminates_native_capture_while_waiting_for_server(self):
        token=StopToken();timer=threading.Timer(.25,token.stop)
        timer.start();self.addCleanup(timer.cancel)
        started=time.monotonic()
        with self.assertRaises(Cancelled):
            Portal._capture_frame_process(self.client.fileno(),1,token)
        self.assertLess(time.monotonic()-started,3)
        self.assert_stopped()

    def test_already_stopped_never_starts_capture(self):
        token=StopToken();token.stop()
        with self.assertRaises(Cancelled):
            Portal._capture_frame_process(self.client.fileno(),1,token)
        self.assertFalse(self.children)


class CapturePipelineTests(unittest.TestCase):
    def test_real_pipewire_stream_through_worker_returns_the_expected_image(self):
        """Private PipeWire + synthetic red video; no screen/audio/device access."""
        from concurrent.futures import ThreadPoolExecutor
        import io
        import json
        import os
        from pathlib import Path
        import shutil
        import tempfile
        from types import SimpleNamespace
        from gi.repository import Gio, GLib
        from PIL import Image
        for command in ('pipewire','pw-dump','pw-link','gst-launch-1.0'):
            if not shutil.which(command):self.skipTest(command+' is not installed')
        with tempfile.TemporaryDirectory(prefix='buddy-capture-') as directory:
            root=Path(directory)
            config=root/'capture.conf'
            config.write_text('''context.properties = { core.daemon = true core.name = buddy-capture-test mem.allow-mlock = false }
context.spa-libs = { support.* = support/libspa-support }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-link-factory }
 { name = libpipewire-module-access }
]
context.objects = [
 { factory = spa-node-factory args = { factory.name = support.node.driver node.name = Dummy-Driver node.group = pipewire.dummy priority.driver = 20000 } }
]
''')
            environment=dict(os.environ,PIPEWIRE_RUNTIME_DIR=str(root),
                PIPEWIRE_REMOTE='buddy-capture-test',XDG_RUNTIME_DIR=str(root))
            with (root/'capture.log').open('w') as log:
                server=subprocess.Popen(['pipewire','-c',str(config)],env=environment,stdout=log,stderr=log)
                producer=None
                try:
                    deadline=time.monotonic()+5
                    while not (root/'buddy-capture-test').exists():
                        self.assertIsNone(server.poll())
                        if time.monotonic()>deadline:raise TimeoutError('Private PipeWire did not start')
                        time.sleep(.02)
                    producer=subprocess.Popen(['gst-launch-1.0','videotestsrc','is-live=true','pattern=red','!',
                        'video/x-raw,format=RGB,width=320,height=240,framerate=25/1','!',
                        'pipewiresink','mode=provide',
                        'stream-properties=props,node.name=BuddyTestVideo,media.class=Video/Source'],
                        env=environment,stdout=log,stderr=log)
                    while True:
                        dump=json.loads(subprocess.check_output(['pw-dump'],env=environment,timeout=5,text=True))
                        nodes=[item for item in dump if item.get('info',{}).get('props',{}).get('node.name')=='BuddyTestVideo']
                        if nodes:node=nodes[0];break
                        if time.monotonic()>deadline:raise TimeoutError('Synthetic video source did not start')
                        time.sleep(.02)
                    portal=object.__new__(Portal);portal.Gio=Gio;portal.GLib=GLib
                    def remote(*args):
                        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                            connection.connect(str(root/'buddy-capture-test'))
                            descriptors=Gio.UnixFDList.new();index=descriptors.append(connection.fileno())
                        return GLib.Variant('(h)',(index,)),descriptors
                    portal.bus=SimpleNamespace(call_with_unix_fd_list_sync=remote)
                    # Exercise both modern serial targeting and older portal IDs.
                    for serial in (node['info']['props']['object.serial'],None):
                        with self.subTest(serial=serial), ThreadPoolExecutor() as pool:
                            result=pool.submit(portal._stream_frame,'/test/session',node['id'],StopToken(),serial)
                            deadline=time.monotonic()+5
                            while True:
                                inputs=subprocess.check_output(['pw-link','-i'],env=environment,timeout=3,text=True).splitlines()
                                if inputs:break
                                if time.monotonic()>deadline:raise TimeoutError('Capture input port did not appear')
                                time.sleep(.02)
                            outputs=subprocess.check_output(['pw-link','-o'],env=environment,timeout=3,text=True).splitlines()
                            self.assertEqual(len(inputs),1);self.assertEqual(len(outputs),1)
                            # This private graph has no desktop session manager;
                            # link its only synthetic output to the capture input.
                            subprocess.run(['pw-link',outputs[0],inputs[0]],env=environment,
                                stdout=log,stderr=log,timeout=3,check=True)
                            image=Image.open(io.BytesIO(result.result(timeout=15)))
                            self.assertEqual(image.size,(320,240))
                            self.assertEqual(image.convert('RGB').getpixel((160,120)),(255,0,0))
                except Exception:
                    log.flush()
                    print((root/'capture.log').read_text()[-3000:])
                    raise
                finally:
                    for process in (producer,server):
                        if process is not None:
                            process.terminate()
                            try:process.wait(timeout=3)
                            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)


if __name__=='__main__':unittest.main()
