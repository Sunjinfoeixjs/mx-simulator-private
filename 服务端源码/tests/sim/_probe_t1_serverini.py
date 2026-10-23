# -*- coding: utf-8 -*-
"""临时探测脚本 1: server.ini 逐字节复现验证 (只读 D: 源文件, 不修改项目文件)"""
import hashlib
import os
import struct
import sys
import zlib

INI = r'D:\mxsj2013\client\server.ini'
sys.path.insert(0, r'C:\Users\Administrator\MX\Claw\tools\sim')
import serverini_encode as se

raw = open(INI, 'rb').read()
print('file=%s' % INI)
print('size=%d' % len(raw))
md5 = hashlib.md5(raw).hexdigest()
print('md5=%s' % md5)
print('expected size=4414 md5=3e1a84e0c65e1f9f59727be5f7360202')
print('size_ok=%s md5_ok=%s' % (len(raw) == 4414, md5 == '3e1a84e0c65e1f9f59727be5f7360202'))
print('-' * 60)

# 复现管线各层
assert raw[:4] == b'-1\r\n'
r = zlib.decompress(raw[4:])
flag = r[0]
n = struct.unpack('<I', r[1:5])[0]
payload = r[5:5 + n]
print('layer1: magic=%r  zlib_out=%dB' % (raw[:4], len(r)))
print('  flag=0x%02X  len_field=%d  payload=%dB  trailing=%dB' % (flag, n, len(payload), len(r) - 5 - n))
plain = bytes((b - se.SKEY[i % 9]) & 0xFF for i, b in enumerate(payload))
text = plain.decode('gbk')
print('layer2: SKEY=%s' % se.SKEY)
print('  plain=%dB  gbk_decode=OK  crlf_lines=%d' % (len(plain), text.count('\r\n') + 1))
print('-' * 60)

# 逐字节复现
re_enc = se.encode(plain, flag)
print('re-encoded size=%d  byte_exact=%s' % (len(re_enc), re_enc == raw))
if re_enc != raw:
    diff = [(i, raw[i], re_enc[i]) for i in range(min(len(raw), len(re_enc))) if raw[i] != re_enc[i]]
    print('  first 10 diffs (off, orig, reenc): %s' % diff[:10])
    print('  total diff bytes=%d' % len(diff))
print('-' * 60)

# 用报告声称的 zlib 参数组合穷举, 确认是否真的 level9/strategy0
best = None
for lvl in range(0, 10):
    for strat in range(0, 5):
        try:
            c = zlib.compressobj(lvl, zlib.DEFLATED, 15, 8, strat)
            z = c.compress(r) + c.flush()
        except Exception:
            continue
        cand = b'-1\r\n' + z
        if cand == raw:
            print('BYTE-EXACT zlib params: level=%d strategy=%d' % (lvl, strat))
            best = (lvl, strat)
if best is None:
    print('no single zlib param combo reproduces byte-exact (level 0-9 x strategy 0-4)')
print('-' * 60)

# 反向: 编码 → 解码 round-trip 明文一致
rt = se.decode(se.encode(plain, flag))[1]
print('encode->decode plain_identical=%s' % (rt == plain))
print('-' * 60)

# 内容抽样
lines = [l for l in text.split('\r\n') if l.strip()]
print('nonempty_lines=%d' % len(lines))
for l in lines[:8]:
    print('  |%s' % l)
print('  ...')
for l in lines[-4:]:
    print('  |%s' % l)
