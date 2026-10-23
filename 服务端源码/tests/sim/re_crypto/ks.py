# -*- coding: utf-8 -*-
"""Keystream oracle: call the original mxmain.dll crypto under Unicorn.

Confirmed call chain (static, non-virtualized):
   SetSeed  0x430A60  __thiscall(this=CNet*, uint32 seed)
       -> 0x430790(seed, &A, &C, &B, &D)         seed expansion
       -> 0x43CAF0(state@+0x80 , A)               init rx state
       -> 0x43CC50(state@+0x80 , NULL, B)         warm-up rx by B bytes
       -> 0x43CAF0(state@+0x1090, C)              init tx state
       -> 0x43CC50(state@+0x1090, NULL, D)        warm-up tx by D bytes
       -> [this+0x72] = 1
   send 0x430AE0 uses state @ +0x1090
   recv 0x430B50 uses state @ +0x80

   transform 0x43CC50 __thiscall(ctx*, uint8* buf, uint len)  in-place XOR
"""
import os
import struct
import sys

# Real DLL only if we ever need it; not required for the fixed transform.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import Dll  # noqa: E402

try:
    from ref_cipher import ref_transform  # noqa: E402
except ImportError:
    ref_transform = None

CNet_THIS = None
SET_SEED = 0x430A60
TRANSFORM = 0x43CC50
OFF_RX = 0x80          # receive keystream state
OFF_TX = 0x1090        # transmit keystream state


class Cipher:
    def __init__(self, seed, dll=None):
        self.dll = dll or Dll()
        self.obj = self.dll.alloc(0x3000, 256, fill=0)
        self.dll.call(SET_SEED, [seed & 0xFFFFFFFF], this=self.obj, timeout=3000000)

    # buf is read/written in place; returns transformed bytes
    def _transform(self, off, data):
        buf = self.dll.alloc(len(data) + 16, 16, data=data)
        self.dll.call(TRANSFORM, [buf, len(data)], this=self.obj + off,
                      timeout=3000000)
        return self.dll.read(buf, len(data))

    def keystream_tx(self, n):
        """tx keystream: encrypting all-zero plaintext yields the keystream."""
        return self._transform(OFF_TX, bytes(n))

    def keystream_rx(self, n):
        return self._transform(OFF_RX, bytes(n))

    def crypt_tx(self, data):
        return self._transform(OFF_TX, bytes(data))

    def crypt_rx(self, data):
        return self._transform(OFF_RX, bytes(data))


class PureCipher(Cipher):
    """Pure-python replacement (no Unicorn) once ref_transform is recovered."""

    def __init__(self, seed):
        from ref_cipher import ref_setseed
        self.tx, self.rx = ref_setseed(seed)

    def _transform(self, off, data):
        st = self.tx if off == OFF_TX else self.rx
        return ref_transform(st, bytes(data))

    def keystream_tx(self, n):
        return self._transform(OFF_TX, bytes(n))

    def keystream_rx(self, n):
        return self._transform(OFF_RX, bytes(n))


if __name__ == '__main__':
    d = Dll()
    c = Cipher(0x12345678, d)
    ks = c.keystream_tx(64)
    print('tx keystream[0:64]:', ks.hex(' '))
    kr = c.keystream_rx(64)
    print('rx keystream[0:64]:', kr.hex(' '))
    # does a second call continue the stream?
    ks2 = c.keystream_tx(64)
    print('tx next  [64:128]:', ks2.hex(' '))
    ks3 = c.keystream_tx(64)
    print('tx next  [128:192]:', ks3.hex(' '))
