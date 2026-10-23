# -*- coding: utf-8 -*-
"""Tests for the local cipher-capture artifact format."""
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest

SIM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim')
sys.path.insert(0, SIM_DIR)
from crypto_capture import recv_window, save_capture


class SaveCaptureTests(unittest.TestCase):
    def test_saves_exact_bytes_and_verifiable_metadata(self):
        payload = bytes.fromhex('01 02 03 ff')
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'sample.bin')
            digest = save_capture(path, payload, {'seed': 0x12345678})

            with open(path, 'rb') as f:
                self.assertEqual(f.read(), payload)
            with open(os.path.join(directory, 'sample.json'), encoding='utf-8') as f:
                metadata = json.load(f)

        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertEqual(metadata['sha256'], digest)
        self.assertEqual(metadata['bytes'], len(payload))
        self.assertEqual(metadata['seed'], 0x12345678)


class RecvWindowTests(unittest.TestCase):
    def test_reads_multiple_chunks_until_timeout(self):
        class FakeSocket:
            def __init__(self):
                self.chunks = [b'first', b'second']
                self.timeouts = []

            def settimeout(self, value):
                self.timeouts.append(value)

            def recv(self, _size):
                if self.chunks:
                    return self.chunks.pop(0)
                raise socket.timeout()

        sock = FakeSocket()
        self.assertEqual(recv_window(sock, 1.0), b'firstsecond')
        self.assertGreaterEqual(len(sock.timeouts), 3)
        self.assertTrue(all(timeout > 0 for timeout in sock.timeouts))


if __name__ == '__main__':
    unittest.main()
