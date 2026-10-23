# -*- coding: utf-8 -*-
"""找出 0x43CC50 的 wrap 重填到底改了对象里的哪些字段（表区以外）。

做法：消费满第一 cycle（cursor=4096）→ 快照整个 0x3000 对象 → 调 1 字节触发
重填 → 再快照 → 只打印表区（+0x88..+0x1087 / +0x1098..+0x2097）以外变化的 4 字节字。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80
OBJ = 0x3000
# 表区（相对 state 偏移 +0x08 起 4096 字节）
RX_TBL = (RX + 8, RX + 8 + 4096)
TX_TBL = (TX + 8, TX + 8 + 4096)


def in_tbl(a):
    return (RX_TBL[0] <= a < RX_TBL[1]) or (TX_TBL[0] <= a < TX_TBL[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='0x12345678')
    args = ap.parse_args()
    seed = int(args.seed, 0)

    d = Dll()
    st = mxc.SEED_START[seed]
    o = d.alloc(OBJ, 512, fill=0)
    d.call(0x430A60, [seed], this=o, timeout=3000000)

    for nm, off, cur in (('RX', RX, st[2]), ('TX', TX, st[3])):
        n1 = 4096 - cur
        buf = d.alloc(n1 + 64, 16, data=bytes(n1 + 64))
        pos = 0
        while pos < n1:
            k = min(144, n1 - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        before = d.read(o, OBJ)
        d.call(0x43CC50, [buf + pos, 1], this=o + off, timeout=3000000)
        after = d.read(o, OBJ)
        print('\n[%s] 重填前后（表区以外）变化的 4 字节字：' % nm)
        seen = set()
        for a in range(0, OBJ - 3, 4):
            if in_tbl(a):
                continue
            b0 = before[a:a + 4]
            b1 = after[a:a + 4]
            if b0 != b1:
                print('    +0x%04X: %s -> %s' % (a, b0.hex(' '), b1.hex(' ')))
                seen.add(a)
        if not seen:
            print('    （无）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
