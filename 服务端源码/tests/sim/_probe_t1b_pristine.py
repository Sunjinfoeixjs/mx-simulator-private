# -*- coding: utf-8 -*-
"""临时探测脚本 2: 对 pristine 副本 server7.txt 做逐字节复现验证"""
import hashlib
import struct
import sys
import zlib

sys.path.insert(0, r'C:\Users\Administrator\MX\Claw\tools\sim')
import serverini_encode as se

CANDS = [
    (r'C:\Users\Administrator\MX\2026-10-13-01-47-23\server7.txt', 'PRISTINE(report source)'),
    (r'C:\Users\Administrator\MX\2026-10-13-01-47-23\extracted\server.ini', 'PRISTINE(extracted)'),
    (r'D:\mxsj2013\client\server.ini', 'LIVE(local takeover)'),
]

for path, label in CANDS:
    try:
        raw = open(path, 'rb').read()
    except Exception as e:
        print('SKIP %s: %s' % (path, e))
        continue
    md5 = hashlib.md5(raw).hexdigest()
    print('=' * 70)
    print('[%s] %s' % (label, path))
    print('  size=%d md5=%s' % (len(raw), md5))
    r = zlib.decompress(raw[4:])
    flag = r[0]
    n = struct.unpack('<I', r[1:5])[0]
    payload = r[5:5 + n]
    plain = bytes((b - se.SKEY[i % 9]) & 0xFF for i, b in enumerate(payload))
    text = plain.decode('gbk')
    print('  zlib_out=%dB flag=0x%02X len=%d payload=%dB trailing=%dB'
          % (len(r), flag, n, len(payload), len(r) - 5 - n))
    print('  plain=%dB lines=%d' % (len(plain), text.count('\r\n') + 1))
    re_enc = se.encode(plain, flag)
    exact = re_enc == raw
    print('  BYTE-EXACT RE-ENCODE: %s (reenc %dB)' % (exact, len(re_enc)))
    if not exact:
        diff = [i for i in range(min(len(raw), len(re_enc))) if raw[i] != re_enc[i]]
        print('    diff_bytes=%d first=%s' % (len(diff), diff[:8]))
    # zlib param sweep
    for lvl in range(10):
        for strat in range(5):
            try:
                c = zlib.compressobj(lvl, zlib.DEFLATED, 15, 8, strat)
                if b'-1\r\n' + c.compress(r) + c.flush() == raw:
                    print('    exact zlib params: level=%d strategy=%d' % (lvl, strat))
            except Exception:
                pass
    # content sanity
    lines = [l for l in text.split('\r\n') if l.strip()]
    servers = [l for l in lines if len(l.split()) >= 4 and not l.split()[0] == 'ip']
    print('  nonempty=%d  server-like lines=%d' % (len(lines), len(servers)))
    print('  head: %s' % ' | '.join(lines[:3]))
    print('  tail: %s' % ' | '.join(lines[-3:]))
    print('  contains 127.0.0.1: %s' % ('127.0.0.1' in text))
