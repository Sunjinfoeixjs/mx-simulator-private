# -*- coding: utf-8 -*-
"""在 mxmain.dll 原始字节中定位战斗公式常数（1.3 / 0.1 / 30.0 等），
并沿「.text 中的 4 字节地址引用」反查引用函数。

不依赖 Ghidra：PE 节表手工解析 + 字节模式扫描 + functions.txt 地址映射。
"""
import re
import struct
import sys

DLL = r'C:\Users\Administrator\MX\客户端数据\samples\mxmain.dll'
FUNCS = r'C:\Users\Administrator\MX\客户端源码\dll_export\functions.txt'
DECOMPILED = r'C:\Users\Administrator\MX\客户端源码\dll_export\decompiled_all.c'

# 目标常数（IEEE754 位模式，小端）
TARGETS = [
    ('f32 1.3',  struct.pack('<f', 1.3)),
    ('f32 0.1',  struct.pack('<f', 0.1)),
    ('f32 30.0', struct.pack('<f', 30.0)),
    ('f32 0.35', struct.pack('<f', 0.35)),   # 全局会心率
    ('f64 1.3',  struct.pack('<d', 1.3)),
    ('f64 0.1',  struct.pack('<d', 0.1)),
    ('f64 30.0', struct.pack('<d', 30.0)),
]


def parse_sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt_size = struct.unpack_from('<H', data, pe + 20)[0]
    image_base = struct.unpack_from('<I', data, pe + 28)[0]
    sec_off = pe + 24 + opt_size
    secs = []
    for i in range(nsec):
        off = sec_off + 40 * i
        name = data[off:off + 8].rstrip(b'\0').decode('ascii', 'replace')
        vsize, vaddr, rawsize, rawoff = struct.unpack_from('<IIII', data, off + 8)
        secs.append(dict(name=name, vaddr=vaddr, vsize=vsize,
                         rawoff=rawoff, rawsize=rawsize))
    return image_base, secs


def off_to_va(secs, image_base, off):
    for s in secs:
        if s['rawoff'] <= off < s['rawoff'] + s['rawsize']:
            return image_base + s['vaddr'] + (off - s['rawoff']), s['name']
    return None, None


def load_functions():
    """functions.txt → [(addr_int, name, size)]，按地址排序。"""
    out = []
    with open(FUNCS, encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3 or not re.fullmatch(r'[0-9a-fA-F]{8}', parts[0]):
                continue
            out.append((int(parts[0], 16), parts[1], int(parts[2])))
    out.sort()
    return out


def find_func(funcs, va):
    """二分：找最后一个 addr <= va 且 va < addr+max(size,32) 的函数。"""
    import bisect
    addrs = [f[0] for f in funcs]
    i = bisect.bisect_right(addrs, va) - 1
    if i < 0:
        return None
    addr, name, size = funcs[i]
    if va < addr + max(size, 32):
        return addr, name, size
    return None


def main():
    data = open(DLL, 'rb').read()
    image_base, secs = parse_sections(data)
    print('ImageBase = 0x%08X' % image_base)
    for s in secs:
        print('  %-8s VA=0x%08X raw=0x%08X size=0x%X' %
              (s['name'], image_base + s['vaddr'], s['rawoff'], s['rawsize']))
    funcs = load_functions()
    print('functions: %d' % len(funcs))
    print()

    # .text 节范围（用于引用扫描）
    text = next(s for s in secs if s['name'] == '.text')
    text_raw = data[text['rawoff']:text['rawoff'] + text['rawsize']]
    text_va = image_base + text['vaddr']

    for label, pat in TARGETS:
        # 1) 常数本体在哪些节出现
        hits = []
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            va, secname = off_to_va(secs, image_base, i)
            hits.append((i, va, secname))
            start = i + 1
        print('== %s  (%s)  共 %d 处 ==' % (label, pat.hex(), len(hits)))
        for off, va, secname in hits[:12]:
            print('  常数 @ fileoff=0x%X VA=0x%08X 节=%s' % (off, va, secname))
        # 2) 对每个落在非 .text 节的常数，扫 .text 找 VA 引用
        for off, va, secname in hits:
            if secname == '.text' or va is None:
                continue
            ref = struct.pack('<I', va)
            rstart = 0
            refs = []
            while True:
                j = text_raw.find(ref, rstart)
                if j < 0:
                    break
                ref_va = text_va + j
                refs.append(ref_va)
                rstart = j + 1
            for ref_va in refs:
                fn = find_func(funcs, ref_va)
                if fn:
                    print('    ↑ 被引用 @ 代码VA=0x%08X  函数 %s (0x%08X size=%d)' %
                          (ref_va, fn[1], fn[0], fn[2]))
                else:
                    print('    ↑ 被引用 @ 代码VA=0x%08X  (函数外?)' % ref_va)
        print()


if __name__ == '__main__':
    main()
