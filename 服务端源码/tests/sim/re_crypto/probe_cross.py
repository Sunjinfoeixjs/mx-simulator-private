# -*- coding: utf-8 -*-
"""确定 cursor 跨 4096 的语义：中途跨过 4096 时是否重填？cursor 存 0..4096 还是取模？

步骤（RX, seed=0x12345678, cur=2654）：
  · 小块消费 1400 → p1 应为 4054
  · 再一次性消费 100（第 42 字节跨过 4096）→ 看是否 fill、p1 = ?、输出是否换表
"""
import sys

sys.path.insert(0, r'C:\Users\Administrator\MX\服务端源码\sim')
from emu import Dll  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn.x86_const import UC_X86_REG_EAX  # noqa: E402
import mx_crypto as mxc  # noqa: E402

M = 0xFFFFFFFF
RX = 0x80
seed = 0x12345678
st = mxc.SEED_START[seed]


def u32(d, a):
    return int.from_bytes(d.read(a, 4), 'little')


d = Dll()
fills = []


def hook(uc, addr, size, ud):
    if addr == 0x43CA7F:
        fills.append(uc.reg_read(UC_X86_REG_EAX) & M)


d.uc.hook_add(UC_HOOK_CODE, hook)
o = d.alloc(0x3000, 512, fill=0)
d.call(0x430A60, [seed], this=o, timeout=3000000)

buf = d.alloc(2048, 16, data=bytes(2048))
p0 = u32(d, o + RX)
p1 = u32(d, o + RX + 4)
print('SetSeed 后: p0=0x%08X p1=%d' % (p0, p1))

# 小块 1400（每块 100）
pos = 0
fills.clear()
while pos < 1400:
    d.call(0x43CC50, [buf + pos, 100], this=o + RX, timeout=3000000)
    pos += 100
print('消费 1400 后: p1=%d  fills=%s' % (u32(d, o + RX + 4), ['0x%08X' % f for f in fills]))

# 一次 100 字节，第 42 字节跨 4096
offs = pos
fills.clear()
d.call(0x43CC50, [buf + offs, 100], this=o + RX, timeout=3000000)
out = d.read(buf + offs, 100)
print('跨 4096 的一次 100 字节调用: fills=%s' % ['0x%08X' % f for f in fills])
print('  调用后 p0=0x%08X p1=%d' % (u32(d, o + RX), u32(d, o + RX + 4)))
print('  输出前 48 = %s' % out[:48].hex(' '))
print('  输出后 52 = %s' % out[48:].hex(' '))

# 对照：换表后应该是什么
t0 = mxc.table_bytes(st[0])
t1 = mxc.table_bytes(0x320F50BC)
# 假设：跨点时若重填，则 [0:42] 来自 t0[4054:4096]，[42:] 来自 t1[0:58]
expect_refill = t0[4054:4096] + t1[:58]
# 假设：不重填，直接 t0 取模
expect_reuse = (t0[4054:4096] + t0[:58])
print('  匹配「中途重填」: %s' % (out == expect_refill))
print('  匹配「复用旧表」: %s' % (out == expect_reuse))
