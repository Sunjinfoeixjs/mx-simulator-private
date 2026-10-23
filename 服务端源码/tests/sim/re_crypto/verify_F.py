# -*- coding: utf-8 -*-
"""Now that the fill routine is a plain function, verify the step chain.

The routine reads only ctx[0] as the start value; it writes 1024 dwords to
ctx+8.  So table = F(start).  Verify F against the stored RX/TX tables using
the captured start values.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EDI  # noqa: E402

M = 0xFFFFFFFF
MASK = 0xDA3A2A9C
PAR = bytes(bin(i).count('1') & 1 for i in range(256))


def step(eax):
    ebx = eax
    eax = (eax << 1) & M
    ebx &= MASK
    edx = ebx
    ebx = int.from_bytes(ebx.to_bytes(4, 'little'), 'big')
    bx = ((ebx & 0xFFFF) ^ (edx & 0xFFFF)) & 0xFFFF
    ebx = (ebx & 0xFFFF0000) | bx
    bh = (ebx >> 8) & 0xFF
    bl = ebx & 0xFF
    ebx = (ebx & 0xFFFF00FF) | ((bh ^ bl) << 8)
    if PAR[ebx & 0xFF] == 0:
        eax = (eax + 1) & M
    return eax


def F(start, n=1024):
    out = []
    eax = start & M
    for _ in range(n):
        for _ in range(32):
            eax = step(eax)
        out.append(eax)
    return out


def main():
    d = Dll()
    for seed in (0x12345678, 0xDEADBEEF):
        uc = d.uc
        starts = []

        def hook(uc_, addr, size, ud):
            if addr == 0x43CA7F:
                starts.append(uc_.reg_read(UC_X86_REG_EAX) & M)

        h = uc.hook_add(UC_HOOK_CODE, hook)
        o = d.alloc(0x3000, 512, fill=0)
        d.call(0x430A60, [seed], this=o, timeout=3000000)
        uc.hook_del(h)

        rx = struct.unpack('<1024I', d.read(o + 0x88, 4096))
        tx = struct.unpack('<1024I', d.read(o + 0x1098, 4096))
        for nm, start, real in (('RX', starts[0], rx), ('TX', starts[1], tx)):
            mine = F(start)
            print('seed=%08X %s start=%08X  F==table: %s' % (seed, nm, start, mine == list(real)))
            if mine != list(real):
                for i in range(1024):
                    if mine[i] != real[i]:
                        print('    diff at %d: mine=%08X dll=%08X' % (i, mine[i], real[i]))
                        break


if __name__ == '__main__':
    main()
