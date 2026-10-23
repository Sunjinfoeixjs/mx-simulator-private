# -*- coding: utf-8 -*-
"""PE 字符串交叉引用定位（32 位）：找目标字符串 → VA → 代码段绝对引用 → 反汇编上下列。

用法:
    python _pe_xref.py <exe路径> <GBK字符串> [上下文字节=64]
"""
import struct
import sys

import pefile
from capstone import CS_ARCH_X86, CS_MODE_32, Cs

path = sys.argv[1]
needle = sys.argv[2]
ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 64

raw = needle.encode('gbk')
data = open(path, 'rb').read()

pe = pefile.PE(path, fast_load=True)
ib = pe.OPTIONAL_HEADER.ImageBase
print('ImageBase = 0x%X；文件大小 = %d' % (ib, len(data)))


def off2va(off):
    for s in pe.sections:
        if s.PointerToRawData <= off < s.PointerToRawData + s.SizeOfRawData:
            return ib + s.VirtualAddress + (off - s.PointerToRawData), s.Name.rstrip(b'\x00').decode()
    return None, None


def va2off(va):
    rva = va - ib
    for s in pe.sections:
        if s.VirtualAddress <= rva < s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData):
            return s.PointerToRawData + (rva - s.VirtualAddress)
    return None


offs, pos = [], 0
while True:
    j = data.find(raw, pos)
    if j < 0:
        break
    offs.append(j)
    pos = j + 1

print('字符串「%s」出现 %d 处' % (needle, len(offs)))
md = Cs(CS_ARCH_X86, CS_MODE_32)

for off in offs:
    va, sec = off2va(off)
    print('\n========= 文件偏移 0x%X（段 %s）→ VA 0x%X =========' % (off, sec, va))
    # 字符串周边（±80B）可打印预览
    seg = data[max(0, off - 80):off + len(raw) + 80]
    txt = seg.decode('gbk', 'replace').replace('\r', ' ').replace('\n', ' ')
    print('  周边文本: %r' % txt)
    if not va:
        continue

    # 在全部段中搜 '绝对引用'（4 字节 LE，形似 push/mov 立即数）
    ref = struct.pack('<I', va)
    hits = []
    for s in pe.sections:
        if not (s.Characteristics & 0x20000000):        # 只看可执行段
            continue
        blob = s.get_data()
        p = 0
        while True:
            k = blob.find(ref, p)
            if k < 0:
                break
            hits.append(s.PointerToRawData + k)
            p = k + 1

    print('  代码段绝对引用: %d 处' % len(hits))
    for h in hits:
        hva, _ = off2va(h)
        start = h - ctx
        code = data[start:h + ctx]
        print('  --- 引用点文件偏移 0x%X / VA 0x%X ---' % (h, hva))
        for ins in md.disasm(code, hva - ctx):
            mark = ' <<<' if ins.address <= hva < ins.address + ins.size else ''
            print('    0x%08X: %-24s %s%s'
                  % (ins.address, ins.mnemonic + ' ' + ins.op_str, '', mark))
