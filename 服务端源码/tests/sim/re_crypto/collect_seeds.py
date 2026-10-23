# -*- coding: utf-8 -*-
"""Collect the full SEED_START table (rx_start, tx_start, B, D) for the seeds
used by mx_crypto, then emit it as literal Python."""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX  # noqa: E402

M = 0xFFFFFFFF
SEEDS = [0x00000000, 0x00000001, 0x00000002, 0x00000003, 0x00000004,
         0x00000005, 0x00000010, 0x000000FF, 0x00000100, 0x00010000,
         0x0A5E0BE4, 0x12345678, 0x80000000, 0xDEADBEEF, 0xFFFFFFFF]


def main():
    d = Dll()
    uc = d.uc
    starts = []

    def hook(uc_, addr, size, ud):
        if addr == 0x43CA7F:
            starts.append(uc_.reg_read(UC_X86_REG_EAX) & M)

    uc.hook_add(UC_HOOK_CODE, hook)
    print('SEED_START = {')
    for seed in SEEDS:
        starts.clear()
        o = d.alloc(0x3000, 512, fill=0)
        d.call(0x430A60, [seed], this=o, timeout=3000000)
        B = struct.unpack('<I', d.read(o + 0x108C, 4))[0]
        Dd = struct.unpack('<I', d.read(o + 0x209C, 4))[0]
        print('    0x%08X: (0x%08X, 0x%08X, %d, %d),'
              % (seed, starts[0], starts[1], B, Dd))
    print('}')


if __name__ == '__main__':
    main()
