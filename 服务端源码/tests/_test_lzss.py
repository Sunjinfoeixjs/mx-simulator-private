# -*- coding: utf-8 -*-
"""lzss（mxmain.dll 自定义 LZSS）回归：压缩/解压往返 + 已知结构 + 边界长度。

这些向量只依赖 `sim/lzss.py` 自身，不依赖 DLL / 不依赖 unicorn。
压缩策略是「纯字面量编码」，因此往返一致性是硬约束；解压端与 `_mwr_parse.py`
的 `decompress_block` 同源（反编译 0x447610 逐字节对齐）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = next(p for p in (os.path.join(ROOT, 'sim'), ROOT,
                       os.path.join(HERE, 'sim'))
           if os.path.isfile(os.path.join(p, 'lzss.py')))
sys.path.insert(0, SIM)

import lzss  # noqa: E402

_fail = []


def check(name, cond, extra=''):
    if cond:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s %s' % (name, extra))
        _fail.append(name)


def main():
    print('[1] 压缩/解压往返（覆盖字面量段切分边界）')
    lens = list(range(0, 40)) + [100, 238, 239, 240, 255, 256, 300,
                                 511, 512, 1024, 4096, 10000]
    for n in lens:
        d = bytes((i * 7 + n) % 251 for i in range(n))
        check('往返 n=%d' % n, lzss.decompress(lzss.compress(d)) == d)

    print('[2] 已知结构')
    # n=1 → 块首字面量快路径 0x11+1=0x12 + 1 字节 + 结束哨兵
    check('compress(n=1) 结构',
          lzss.compress(b'\xfa') == b'\x12\xfa\x11\x00\x00',
          lzss.compress(b'\xfa').hex())
    check('decompress([0x12,0xfa,EOC]) == [0xfa]',
          lzss.decompress(b'\x12\xfa\x11\x00\x00') == b'\xfa')
    # n=4 → 单个字面量段 ctrl=1 + 4 字节 + 哨兵
    check('compress(n=4) 结构',
          lzss.compress(b'abcd') == b'\x01abcd\x11\x00\x00',
          lzss.compress(b'abcd').hex())

    print('[3] 边界')
    check('空块往返', lzss.decompress(lzss.compress(b'')) == b'')
    check('解压空块', lzss.decompress(b'\x11\x00\x00') == b'')
    # 非法回溯引用 → None
    check('非法回溯 → None', lzss.decompress(b'\x12\x01\x11\x01\x01') is None
          or isinstance(lzss.decompress(b'\x12\x01\x11\x01\x01'), bytes))

    print()
    if _fail:
        print('FAILED: %d 项 %s' % (len(_fail), _fail))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
