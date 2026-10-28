import errno
import socket
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from desktop_buddy.diagnostics import socket_probe, command_probe,screen_activity_probe


class DiagnosticsTests(unittest.TestCase):
    def test_screen_saver_activity_is_not_equated_with_password_lock(self):
        bus=MagicMock()
        for active,status in ((True,'active'),(False,'inactive')):
            bus.call_sync.return_value.unpack.return_value=(active,)
            self.assertEqual(screen_activity_probe(bus)['status'],status)
        bus.call_sync.side_effect=RuntimeError('session bus unavailable')
        self.assertEqual(screen_activity_probe(bus)['status'],'unknown')
    def test_denied_socket_is_not_an_absent_service(self):
        for code in (errno.EPERM, errno.EACCES):
            with self.subTest(code=code), patch('socket.socket', side_effect=OSError(code, 'denied')):
                self.assertEqual(socket_probe(('127.0.0.1', 1234))['status'], 'blocked')

    def test_refused_and_timeout_have_different_meanings(self):
        for error, status in [(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'), 'unavailable'),
                              (TimeoutError('timed out'), 'unknown')]:
            with self.subTest(status=status), patch('socket.socket') as factory:
                factory.return_value.__enter__.return_value.connect.side_effect = error
                self.assertEqual(socket_probe(('127.0.0.1', 1234))['status'], status)
                factory.return_value.__exit__.assert_called_once()

    def test_gpu_error_does_not_invent_host_hardware_state(self):
        result = subprocess.CompletedProcess(['nvidia-smi'], 1, 'driver inaccessible', '')
        with patch('shutil.which', return_value='/usr/bin/nvidia-smi'), patch('subprocess.run', return_value=result):
            observed = command_probe(['nvidia-smi'])
        self.assertEqual(observed['status'], 'unknown')
        self.assertEqual(observed['reason'], 'driver inaccessible')

    def test_docker_probes_real_socket_not_mode_bits(self):
        with patch('socket.socket') as factory:
            result = socket_probe('/var/run/docker.sock', socket.AF_UNIX)
            factory.return_value.__enter__.return_value.connect.assert_called_once_with('/var/run/docker.sock')
        self.assertEqual(result['status'], 'reachable')
