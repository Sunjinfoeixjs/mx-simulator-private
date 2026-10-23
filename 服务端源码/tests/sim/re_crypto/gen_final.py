# -*- coding: utf-8 -*-
"""The generator is a chained loop:
    eax = word
    repeat 1024:
        repeat 32:  eax = step(eax)
        store eax ; eax continues into the next outer iteration
So table[i] = state after 32*(i+1) steps, starting from `word`.

There were 2048 iterations recorded because SetSeed calls the fill routine
for both contexts (2 x 1024).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emu import Dll  # noqa: E402

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


def gen_table(word, n=1024):
    out = []
    eax = word & M
    for _ in range(n):
        for _ in range(32):
            eax = step(eax)
        out.append(eax)
    return out


def main():
    d = Dll()
    allok = True
    for seed in (0x12345678, 0xDEADBEEF, 0x00000001, 0xFFFFFFFF, 0, 0x0A5E0BE4):
        o = d.alloc(0x3000, 512, fill=0)
        d.call(0x430A60, [seed], this=o, timeout=3000000)
        parts = []
        for nm, off in (('RX', 0x80), ('TX', 0x1090)):
            w = struct.unpack('<I', d.read(o + off, 4))[0]
            real = d.read(o + off + 8, 4096)
            mine = struct.pack('<1024I', *gen_table(w))
            ok = mine == real
            allok &= ok
            parts.append('%s:%s' % (nm, 'OK' if ok else 'FAIL'))
            if not ok:
                for i in range(1024):
                    a = struct.unpack_from('<I', real, i * 4)[0]
                    b = struct.unpack_from('<I', mine, i * 4)[0]
                    if a != b:
                        print('   %s diff at word %d: dll=%08X mine=%08X' % (nm, i, a, b))
                        break
        print('seed=%08X  %s' % (seed, '  '.join(parts)))
    print('\nALL OK' if allok else '\nFAILED')


if __name__ == '__main__':
    main()
