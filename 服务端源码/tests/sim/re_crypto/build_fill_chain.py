# -*- coding: utf-8 -*-
"""后台采集单个方向的 wrap 重填 start 链，增量写 JSON（可断点续跑）。

每个 fill = 4096 字节密钥流；用 256 字节小包调用 0x43CC50（>448 的大调用在模拟器里
不重填，是伪影，不能用）。

用法:
  python build_fill_chain.py --dir rx --fills 128 --out chain_rx_12345678.json
  python build_fill_chain.py --dir tx --fills 128 --out chain_tx_12345678.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')

from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX  # noqa: E402
import mx_crypto as mxc  # noqa: E402

OFF = {'rx': 0x80, 'tx': 0x1090}
M = 0xFFFFFFFF
CHUNK = 256


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', choices=['rx', 'tx'], required=True)
    ap.add_argument('--seed', default='0x12345678')
    ap.add_argument('--fills', type=int, default=128)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    seed = int(args.seed, 0)
    st = mxc.SEED_START[seed]
    off = OFF[args.dir]
    cur = st[2] if args.dir == 'rx' else st[3]

    chain = []
    if os.path.isfile(args.out):
        try:
            chain = json.load(open(args.out))['chain']
            print('resume from %d fills' % (len(chain) - 1))
        except (ValueError, KeyError, OSError):
            chain = []

    d = Dll()
    fills = []

    def hook(uc, addr, size, ud):
        if addr == 0x43CA7F:
            fills.append(uc.reg_read(UC_X86_REG_EAX) & M)

    # 只在生成器函数体内触发 hook（全地址 hook 会拖慢整个 VM 执行 ~20 倍）
    d.uc.hook_add(UC_HOOK_CODE, hook, begin=0x43CA70, end=0x43CAB1)
    o = d.alloc(0x3000, 512, fill=0)
    fills.clear()
    d.call(0x430A60, [seed], this=o, timeout=3000000)
    s0 = fills[0] if args.dir == 'rx' else fills[1]
    if not chain:
        chain.append(s0)

    total = (4096 - cur) + 4096 * args.fills
    buf = d.alloc(total + CHUNK + 16, 16, data=bytes(total + CHUNK + 16))
    # 先快进已采到的 fill（重跑时保持一致）
    pos = 0
    want = len(chain) - 1
    fills.clear()
    while pos < (4096 - cur) + 4096 * want:
        k = min(CHUNK, (4096 - cur) + 4096 * want - pos)
        d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
        pos += k

    t0 = time.time()
    while len(chain) - 1 < args.fills:
        fills.clear()
        end = (4096 - cur) + 4096 * len(chain)
        while pos < end:
            k = min(CHUNK, end - pos)
            d.call(0x43CC50, [buf + pos, k], this=o + off, timeout=3000000)
            pos += k
        chain.extend(fills)
        json.dump({'seed': seed, 'dir': args.dir, 'chain': chain},
                  open(args.out, 'w'))
        el = time.time() - t0
        print('fill %d/%d  new=%s  %.1fs'
              % (len(chain) - 1, args.fills,
                 ['0x%08X' % x for x in fills], el), flush=True)
    print('DONE %s fills=%d -> %s' % (args.dir, len(chain) - 1, args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
