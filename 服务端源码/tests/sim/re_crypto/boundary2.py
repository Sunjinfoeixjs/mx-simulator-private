# -*- coding: utf-8 -*-
"""Quantify the safety boundary honestly.

Find the length up to which the DLL output is CONSISTENT across repeated
fresh runs (that is the only region a server can rely on).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80


def main():
    d = Dll()
    seed = 0x12345678
    print('Repeated fresh runs vs mx_crypto, per direction:')
    for nm, off, cur, start in (('RX', RX, mxc.SEED_START[seed][2], mxc.SEED_START[seed][0]),
                                ('TX', TX, mxc.SEED_START[seed][3], mxc.SEED_START[seed][1])):
        tbl = mxc.table_bytes(start)
        runs = []
        for k in range(6):
            o = d.alloc(0x3000, 512, fill=0)
            d.call(0x430A60, [seed], this=o, timeout=3000000)
            buf = d.alloc(4096, 16, data=bytes(4096))
            d.call(0x43CC50, [buf, 4096], this=o + off, timeout=3000000)
            runs.append(d.read(buf, 4096))
        # first index where runs disagree with each other
        n = len(runs[0])
        diverge = None
        for i in range(n):
            if any(r[i] != runs[0][i] for r in runs[1:]):
                diverge = i
                break
        # first index where runs disagree with model
        model = bytes(tbl[(cur + i) & 0xFFF] for i in range(n))
        mdiff = next((i for i in range(n) if any(r[i] != model[i] for r in runs)), None)
        print('  %s: runs self-consistent up to %s ; model matches up to %s'
              % (nm, diverge if diverge is not None else n,
                 mdiff if mdiff is not None else n))


if __name__ == '__main__':
    main()
