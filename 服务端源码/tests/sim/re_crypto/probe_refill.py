# -*- coding: utf-8 -*-
"""捕获 0x43CA70 每一次 fill 的 start（含 cursor==4096 后的重填），
验证「重填 start」是否等于上一张表的最后一个 word（链式推进）。

只做测量。用法: python probe_refill.py [--seed 0x12345678]
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
    rx_start, tx_start, rx_cur, tx_cur = st[0], st[1], st[2], st[3]

    o = d.alloc(0x3000, 512, fill=0)
    starts.clear()
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    print('seed=0x%08X' % seed)
    print('SetSeed 期间 fill starts: %s' % ['0x%08X' % s for s in starts])
    print('  表内记录 RX=0x%08X TX=0x%08X' % (rx_start, tx_start))

    for nm, off, cur, start in (('RX', RX, rx_cur, rx_start),
                                ('TX', TX, tx_cur, tx_start)):
        n1 = 4096 - cur                      # 第一张表剩余字节
        buf = d.alloc(n1 + 16, 16, data=bytes(n1))
        starts.clear()
        pos = 0
        while pos < n1:                      # 分块消费，避免大调用伪影
            k = min(144, n1 - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        print('\n[%s] 消费第一 cycle %d 字节，期间 fill: %s'
              % (nm, n1, ['0x%08X' % s for s in starts]))

        buf2 = d.alloc(128, 16, data=bytes(128))
        starts.clear()
        d.call(0x43CC50, [buf2, 128], this=o + off, timeout=3000000)
        print('  wrap 触发重填 start: %s' % ['0x%08X' % s for s in starts])

        t1 = mxc.gen_table(start)
        print('  gen_table(原 start)[-1] = 0x%08X' % t1[-1])

        if starts:
            s2 = starts[0]
            ks = d.read(buf2, 128)
            tb2 = mxc.table_bytes(s2)
            ok = ks == tb2[:128]
            print('  重填表 keystream == gen_table(重填 start) ? %s' % ok)
            print('    dll   [0:16] = %s' % ks[:16].hex(' '))
            print('    table2[0:16] = %s' % tb2[:16].hex(' '))
            print('  s2 == gen_table(原start)[-1] ? %s (%s vs %08X)'
                  % (s2 == t1[-1],
                     '0x%08X' % s2, t1[-1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
