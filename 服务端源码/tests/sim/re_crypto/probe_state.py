# -*- coding: utf-8 -*-
"""看 state 字段：param_1[0](+0x00) / param_1[1](+0x04) 在每次 fill 前后的值，
并与 0x43CA70 实际收到的 start（hook 0x43CA7F）对齐，找出「下一张表 start」的来源。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX  # noqa: E402
import mx_crypto as mxc  # noqa: E402

TX, RX = 0x1090, 0x80
M = 0xFFFFFFFF


def u32(d, a):
    return int.from_bytes(d.read(a, 4), 'little')


def show(d, o, off, tag):
    print('    %-22s p0=0x%08X  p1=0x%08X' % (tag, u32(d, o + off), u32(d, o + off + 4)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='0x12345678')
    args = ap.parse_args()
    seed = int(args.seed, 0)

    d = Dll()
    uc = d.uc
    fills = []

    def hook(uc_, addr, size, ud):
        if addr == 0x43CA7F:
            fills.append(uc_.reg_read(UC_X86_REG_EAX) & M)

    uc.hook_add(UC_HOOK_CODE, hook)
    st = mxc.SEED_START[seed]
    o = d.alloc(0x3000, 512, fill=0)

    fills.clear()
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    print('SetSeed: fills=%s' % ['0x%08X' % f for f in fills])

    for nm, off, cur in (('RX', RX, st[2]), ('TX', TX, st[3])):
        print('\n[%s] x86 状态字段（p0=+0x00, p1=+0x04）' % nm)
        show(d, o, off, 'SetSeed 后')
        n1 = 4096 - cur
        buf = d.alloc(8192 + 16, 16, data=bytes(8192))
        pos = 0
        while pos < n1:
            k = min(144, n1 - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        show(d, o, off, '第一 cycle 消费完')

        fills.clear()
        d.call(0x43CC50, [buf + pos, 1], this=o + off, timeout=3000000)
        pos += 1
        print('    wrap 重填 fill start = %s' % ['0x%08X' % f for f in fills])
        show(d, o, off, '重填后')

        # 再消费到下一次 wrap 前，看看 p0/p1
        q = 0
        while q < 4095:
            k = min(256, 4095 - q)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
            q += k
        show(d, o, off, '第二 cycle 消费完')
        fills.clear()
        d.call(0x43CC50, [buf + pos, 1], this=o + off, timeout=3000000)
        pos += 1
        print('    第二次 wrap 重填 fill start = %s' % ['0x%08X' % f for f in fills])
        show(d, o, off, '重填后')
    return 0


if __name__ == '__main__':
    sys.exit(main())
