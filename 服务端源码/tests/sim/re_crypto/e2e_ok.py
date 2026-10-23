# -*- coding: utf-8 -*-
"""FINAL validation: mx_crypto vs original mxmain.dll, using each
(seed,direction)'s measured deterministic limit."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80


def main():
    d = Dll()
    rng = random.Random(2026)
    fails = 0
    checks = 0
    total = 0

    def setup(seed):
        o = d.alloc(0x3000, 512, fill=0)
        d.call(0x430A60, [seed], this=o, timeout=3000000)
        return o

    def dll_crypt(o, off, data):
        buf = d.alloc(len(data) + 16, 16, data=bytes(data))
        d.call(0x43CC50, [buf, len(data)], this=o + off, timeout=3000000)
        return d.read(buf, len(data))

    for seed in sorted(mxc.SEED_START):
        rx_lim, tx_lim = mxc._USABLE[seed]
        for nm, off, meth, lim in (('TX', TX, 'crypt_tx', tx_lim),
                                   ('RX', RX, 'crypt_rx', rx_lim)):
            for _ in range(8):
                n = rng.randrange(1, lim)
                pt = bytes(rng.randrange(256) for _ in range(n))
                real = dll_crypt(setup(seed), off, pt)
                mine = getattr(mxc.RealCipher(seed), meth)(pt)
                checks += 1
                total += n
                if real != mine:
                    fails += 1
                    bad = next(i for i in range(n) if real[i] != mine[i])
                    print('  %s seed=%08X len=%d MISMATCH at %d' % (nm, seed, n, bad))
                real0 = dll_crypt(setup(seed), off, bytes(n))
                mine0 = getattr(mxc.RealCipher(seed), meth)(bytes(n))
                checks += 1
                if real0 != mine0:
                    fails += 1
                    print('  %s seed=%08X keystream mismatch' % (nm, seed))
        print('seed=%08X  rx=%3d tx=%3d  %s'
              % (seed, rx_lim, tx_lim, 'OK' if fails == 0 else 'FAIL'))

    for seed in sorted(mxc.SEED_START):
        msg = b'\x05C2S_LOGIN_ACCOUNT_ENTER'
        enc = mxc.RealCipher(seed).crypt_tx(msg)
        if mxc.RealCipher(seed).crypt_tx(enc) != msg:
            fails += 1
            print('roundtrip fail %08X' % seed)

    print('\nchecks=%d bytes=%d fails=%d' % (checks, total, fails))
    print('==== %s ====' % ('ALL CROSS-CHECKED OK' if fails == 0 else 'FAILURES'))
    return 0 if fails == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
