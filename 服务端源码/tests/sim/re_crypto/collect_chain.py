# -*- coding: utf-8 -*-
"""采集「wrap 重填」的 start 链（每张表 4096 字节），供 mx_crypto 生成任意长密钥流。

背景（probe_* 实测）：
  · 0x43CC50 是连续异或流，cursor 0..4095，跨过 4096 时**重填**一张新表；
  · 新表的 start 由 VM 内部算出（S_{i+1} = f(gen_table(S_i)[-1])），f 无法解析，
    故与 SEED_START 一样用**实测真值表**；
  · 真实客户端按小包（<448B）调用，重填在小包内正确发生；大缓冲单调用是模拟器伪影。

用法:
  python collect_chain.py --seed 0x12345678 --fills 16      # 采集并打印
  python collect_chain.py --seed 0x12345678 --fills 256 --emit  # 输出 Python 字面量
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
CHUNK = 256          # < 448，确保重填在小包内正确发生


def collect(seed, n_fills):
    st = mxc.SEED_START[seed]
    d = Dll()
    fills = []

    def hook(uc, addr, size, ud):
        if addr == 0x43CA7F:
            fills.append(uc.reg_read(UC_X86_REG_EAX) & M)

    d.uc.hook_add(UC_HOOK_CODE, hook)
    o = d.alloc(0x3000, 512, fill=0)

    fills.clear()
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    s_rx, s_tx = fills[0], fills[1]

    out = {}
    for nm, off, cur, s0 in (('rx', RX, st[2], s_rx), ('tx', TX, st[3], s_tx)):
        chain = [s0]
        total = (4096 - cur) + 4096 * n_fills
        buf = d.alloc(total + CHUNK + 16, 16, data=bytes(total + CHUNK + 16))
        fills.clear()
        pos = 0
        while pos < total:
            k = min(CHUNK, total - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        chain += fills
        out[nm] = chain[:n_fills + 1]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='0x12345678')
    ap.add_argument('--fills', type=int, default=16)
    ap.add_argument('--emit', action='store_true')
    args = ap.parse_args()
    seed = int(args.seed, 0)

    ch = collect(seed, args.fills)
    print('seed=0x%08X  rx_fills=%d tx_fills=%d' % (seed, len(ch['rx']), len(ch['tx'])))
    print('  rx[0:4] = %s' % ['0x%08X' % s for s in ch['rx'][:4]])
    print('  tx[0:4] = %s' % ['0x%08X' % s for s in ch['tx'][:4]])
    if args.emit:
        print('\nFILL_CHAIN[0x%08X] = {' % seed)
        for nm in ('rx', 'tx'):
            print("    '%s': [" % nm)
            row = ch[nm]
            for i in range(0, len(row), 4):
                print('        ' + ', '.join('0x%08X' % x for x in row[i:i + 4]) + ',')
            print('    ],')
        print('}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
