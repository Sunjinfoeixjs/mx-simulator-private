# -*- coding: utf-8 -*-
"""采集 0x43CC50 每次 wrap 重填的 start 链（s0, s1, s2, ...），并尝试
找出递推关系 s_{i+1} = g(s_i)。

0x43CA70 只读 param_1[0] 写表到 param_1[2..]，不回写 start —— 所以
"下一张表的 start" 是 0x43CC50（VM）自己算出来写回 param_1[0] 的。
本脚本黑盒采集它，再暴力试 s_{i+1} 是否 = _step^k(s_i)。
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


def read_u32(d, addr):
    return int.from_bytes(d.read(addr, 4), 'little')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='0x12345678')
    args = ap.parse_args()
    seed = int(args.seed, 0)

    d = Dll()
    uc = d.uc
    starts = []

    def hook(uc_, addr, size, ud):
        if addr == 0x43CA7F:
            starts.append(uc_.reg_read(UC_X86_REG_EAX) & M)

    uc.hook_add(UC_HOOK_CODE, hook)
    st = mxc.SEED_START[seed]

    o = d.alloc(0x3000, 512, fill=0)
    d.call(0x430A60, [seed], this=o, timeout=3000000)

    for nm, off, cur in (('RX', RX, st[2]), ('TX', TX, st[3])):
        chain = []
        pos = 0
        # 先消费第一 cycle（剩余字节）
        n1 = 4096 - cur
        buf = d.alloc(4096 + 16, 16, data=bytes(4096))
        while pos < n1:
            k = min(144, n1 - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        chain.append(read_u32(d, o + off))       # s0
        # 之后每 4096 字节触发一次重填
        for _ in range(4):
            starts.clear()
            q = 0
            while q < 4096:
                k = min(256, 4096 - q)
                d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
                pos += k
                q += k
            chain.append(read_u32(d, o + off))
        print('[%s] start 链: %s' % (nm, ['0x%08X' % s for s in chain]))
        # 尝试 s_{i+1} = _step^k(s_i)
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            v = a
            hit = None
            for k in range(0, 2000000):
                if v == b:
                    hit = k
                    break
                v = mxc._step(v)
            print('  s%d(0x%08X) -> s%d(0x%08X): step^k 命中 k=%s'
                  % (i, a, i + 1, b, hit))
    return 0


if __name__ == '__main__':
    sys.exit(main())
