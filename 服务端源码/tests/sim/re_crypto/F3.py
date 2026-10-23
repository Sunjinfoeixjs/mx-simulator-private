# -*- coding: utf-8 -*-
"""FINAL keystream generator with the corrected predicate:

    if parity(xor_result_low_byte) is ODD:   eax += 1
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX  # noqa: E402

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
    if PAR[bh ^ bl]:          # ODD parity -> inc
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
    got = F(0x30881D1B, 4)
    exp = [0xFE583FE5, 0x622D1EC4, 0x5A7AE70C, 0x2A3F61F4]
    print('chain:', ['%08X' % x for x in got])
    print('exp  :', ['%08X' % x for x in exp])
    print('match:', got == exp)

    d = Dll()
    uc = d.uc
    starts = []

    def hook(uc_, addr, size, ud):
        if addr == 0x43CA7F:
            starts.append(uc_.reg_read(UC_X86_REG_EAX) & M)

    uc.hook_add(UC_HOOK_CODE, hook)
    ok_all = True
    for seed in (0x12345678, 0xDEADBEEF, 1, 0, 0xFFFFFFFF):
        o = d.alloc(0x3000, 512, fill=0)
        starts.clear()
        d.call(0x430A60, [seed], this=o, timeout=3000000)
        rx = list(struct.unpack('<1024I', d.read(o + 0x88, 4096)))
        tx = list(struct.unpack('<1024I', d.read(o + 0x1098, 4096)))
        r1 = F(starts[0]) == rx
        r2 = F(starts[1]) == tx
        ok_all &= r1 and r2
        print('seed=%08X  RX:%s (start %08X)  TX:%s (start %08X)'
              % (seed, 'OK' if r1 else 'FAIL', starts[0],
                 'OK' if r2 else 'FAIL', starts[1]))
    print('\nALL OK' if ok_all else '\nFAILED')


if __name__ == '__main__':
    main()
