# -*- coding: utf-8 -*-
"""扫 D:\\mxsj2013\\client 下所有 exe/dll，定位战斗公式常数的浮点位模式。
只报「存在性 + 出现次数 + 节名」，不做引用反查（mx.exe/gameplayer.exe 无现成反编译）。"""
import os
import struct
import sys

TARGETS = [
    ('f32 1.3',  struct.pack('<f', 1.3)),
    ('f32 0.1',  struct.pack('<f', 0.1)),
    ('f32 30.0', struct.pack('<f', 30.0)),
    ('f32 0.35', struct.pack('<f', 0.35)),
    ('f64 1.3',  struct.pack('<d', 1.3)),
    ('f64 0.1',  struct.pack('<d', 0.1)),
    ('f64 30.0', struct.pack('<d', 30.0)),
    ('f64 0.35', struct.pack('<d', 0.35)),
]

ROOTS = [r'D:\mxsj2013\client']
EXTRA = [r'C:\Users\Administrator\MX\客户端数据\samples\mxmain.dll']


def parse_sections(data):
    try:
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            return None, []
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        opt_size = struct.unpack_from('<H', data, pe + 20)[0]
        magic = struct.unpack_from('<H', data, pe + 24)[0]
        if magic == 0x10B:
            image_base = struct.unpack_from('<I', data, pe + 28)[0]
        else:
            image_base = struct.unpack_from('<Q', data, pe + 24 + 8)[0]
        sec_off = pe + 24 + opt_size
        secs = []
        for i in range(nsec):
            off = sec_off + 40 * i
            if off + 40 > len(data):
                break
            name = data[off:off + 8].rstrip(b'\0').decode('ascii', 'replace')
            vsize, vaddr, rawsize, rawoff = struct.unpack_from('<IIII', data, off + 8)
            secs.append((name, vaddr, vsize, rawoff, rawsize))
        return image_base, secs
    except Exception:
        return None, []


def sec_of(secs, off):
    for name, vaddr, vsize, rawoff, rawsize in secs:
        if rawoff <= off < rawoff + rawsize:
            return name
    return 'overlay' if off < len(data) else '?'


def scan(path):
    data = open(path, 'rb').read()
    _, secs = parse_sections(data)
    print('--- %s (%d bytes) ---' % (path, len(data)))
    for label, pat in TARGETS:
        n = 0
        secnames = {}
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            n += 1
            sn = sec_of(secs, i) if secs else '?'
            secnames[sn] = secnames.get(sn, 0) + 1
            start = i + 1
        mark = '★' if n else ' '
        print('  %s %-9s : %d %s' % (mark, label, n, secnames if n else ''))
    print()


def main():
    files = list(EXTRA)
    for root in ROOTS:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if n.lower().endswith(('.exe', '.dll')):
                    files.append(os.path.join(dirpath, n))
    for f in files:
        try:
            scan(f)
        except Exception as e:
            print('--- %s  ERROR %r ---' % (f, e))


if __name__ == '__main__':
    main()
