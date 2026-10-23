# -*- coding: utf-8 -*-
"""Measure per-(seed,direction) usable length as the MINIMUM over repeated
fresh runs (deterministic limit).  Emit the corrected table."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80


def main():
    d = Dll()
    res = {}
    for seed in sorted(mxc.SEED_START):
        st = mxc.SEED_START[seed]
        for nm, off, cur, start in (('RX', RX, st[2], st[0]),
                                    ('TX', TX, st[3], st[1])):
            tbl = mxc.table_bytes(start)
            best = 4096
            for _ in range(5):
                o = d.alloc(0x3000, 512, fill=0)
                d.call(0x430A60, [seed], this=o, timeout=3000000)
                buf = d.alloc(2048, 16, data=bytes(2048))
                d.call(0x43CC50, [buf, 2048], this=o + off, timeout=3000000)
                ks = d.read(buf, 2048)
                u = next((i for i in range(2048)
                          if ks[i] != tbl[(cur + i) & 0xFFF]), 2048)
                best = min(best, u)
            res.setdefault(seed, {})[nm] = best
    print('_USABLE = {')
    for seed in sorted(res):
        print('    0x%08X: (%d, %d),' % (seed, res[seed]['RX'], res[seed]['TX']))
    print('}')


if __name__ == '__main__':
    main()
