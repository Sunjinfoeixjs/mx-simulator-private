# -*- coding: utf-8 -*-
"""验证「链式密钥流模型」== DLL（小包调用，跨多张表）。

chained = table(S0)[cur:] + table(S1) + table(S2) + ...
DLL      = 分 256 字节小包调用 0x43CC50 攒出来的密钥流
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402
import collect_chain as cc  # noqa: E402

TX, RX = 0x1090, 0x80


def dll_keystream(seed, off, cur, n, chunk=256):
    d = Dll()
    o = d.alloc(0x3000, 512, fill=0)
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    buf = d.alloc(n + chunk + 16, 16, data=bytes(n + chunk + 16))
    pos = 0
    while pos < n:
        k = min(chunk, n - pos)
        d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
        pos += k
    return d.read(buf, n)


def chained(chain, cur, n):
    out = bytearray()
    out += mxc.table_bytes(chain[0])[cur:4096]
    idx = 1
    while len(out) < n:
        out += mxc.table_bytes(chain[idx])
        idx += 1
    return bytes(out[:n])


def main():
    seed = 0x12345678
    n_fills = 4
    chain = cc.collect(seed, n_fills)
    st = mxc.SEED_START[seed]
    for nm, off, cur in (('RX', RX, st[2]), ('TX', TX, st[3])):
        n = 4096 * n_fills
        dll = dll_keystream(seed, off, cur, n)
        model = chained(chain[nm.lower()], cur, n)
        first = next((i for i in range(n) if dll[i] != model[i]), None)
        print('[%s] n=%d  首个不一致: %s  %s'
              % (nm, n, first, 'IDENTICAL' if first is None else 'MISMATCH'))
        if first is not None:
            print('    dll  : %s' % dll[first:first + 16].hex(' '))
            print('    model: %s' % model[first:first + 16].hex(' '))
    return 0


if __name__ == '__main__':
    sys.exit(main())
