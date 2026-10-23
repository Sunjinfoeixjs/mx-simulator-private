"""录像代际判定：旧族（LSB 打包期）必须被默认剔除，否则统计会再次被污染。

背景见 `协议资料/报告/客户端网络层解析_20261018.md` §5.3–5.4：
137 份 `.mwr` 里有 8 份录自「旧模拟服按 LSB 打包位域」的时期，字节恒为 `0x40/0x60`，
按正确（MSB）位序解出来是 hurt=4/6 这种非法值。它们与已作废的「野狼必红」结论直接相关
（族别与造型恰好重合），混进统计会再次把编码差异读成怪物差异。

判据**不是文件序号**（那会随新录像失效），而是「这个字节在正确编码下不可能出现」：
客户端 `UnpackBit(0,4)` 只用到 hurt ∈ {0,1}，所以 `(bits>>4)&0xF > 1` 一定是错编码。
"""
import os
import sys

sys.path.insert(0, r'C:\Users\Administrator\MX\客户端源码\tools')
import _mwr_export as X

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


def ws(bits):
    return {'op': 123, 'a_bits': bits}


print('=' * 70)
print(' A. 位域合法性判据')
print('=' * 70)
for bits, want, why in ((0x01, True, '普通受击 hurt=0/life=0/ward=1'),
                        (0x09, True, '致死 hurt=0/life=2/ward=1'),
                        (0x11, True, '会心 hurt=1/life=0/ward=1'),
                        (0x40, False, '旧族普通（高位 4 = 非法 hurt）'),
                        (0x60, False, '旧族致死（高位 6 = 非法 hurt）')):
    check(X.wstate_bits_valid(bits) is want,
          '0x%02X %-28s → %s' % (bits, why, '合法' if want else '非法'))

print()
print('=' * 70)
print(' B. 整份录像的代际判定')
print('=' * 70)
check(X.classify_file([])[0] == 'unknown', '空录像 → unknown（保留，污染不了位域统计）')
check(X.classify_file([{'op': 112}, {'op': 115}])[0] == 'unknown',
      '无 WState 帧 → unknown（如纯移动/商店录像）')
check(X.classify_file([ws(0x01), ws(0x09), ws(0x11)])[0] == 'current',
      '全部合法 → current')
fam, why = X.classify_file([ws(0x01), ws(0x40), ws(0x01)])
check(fam == 'legacy', '只要出现一帧非法 → 整份 legacy（%r）' % why)
check('1/3' in why, '理由里带「非法帧数/总帧数」：%r' % why)

print()
print('=' * 70)
print(' B2. WarAdd 布局代际（allied 位在 bit7 还是 bit0）')
print('=' * 70)


def wadd(uid, shape, bits, name):
    body = bytes([uid]) + shape.to_bytes(2, 'little') + b'\x00' + bytes([bits])
    body += bytes([0, 0, 1]) + name.encode('gbk') + b'\x00'
    return body


new_frame = wadd(100, 1, 0x80, '阿飞')          # 新版：友方 allied 在 bit7
old_frame = wadd(0, 1, 0x01, '阿飞')            # 旧版：友方 allied 在 bit0
lay_new = X.detect_layout([(0, 115, new_frame), (1, 115, wadd(1, 2130, 0x00, '野狼'))])
lay_old = X.detect_layout([(0, 115, old_frame), (1, 115, wadd(1, 101, 0x00, '野猪'))])
check(lay_new['era'] == 'new' and lay_new['allied_bit'] == 0x80,
      '新版（友方 0x80）→ allied_bit=0x80')
check(lay_old['era'] == 'old' and lay_old['allied_bit'] == 0x01,
      '旧版（友方 0x01）→ allied_bit=0x01')
r_new = X.row_of('x', 0, 115, new_frame, lay_new)
r_old = X.row_of('x', 0, 115, old_frame, lay_old)
check(r_new['a_allied'] == 1 and r_old['a_allied'] == 1,
      '两代都能解出「友方 allied=1」（新版 %r / 旧版 %r）'
      % (r_new['a_allied'], r_old['a_allied']))
check(r_new['a_name'] == '阿飞' and r_old['a_name'] == '阿飞',
      '两代名字都能解（%r / %r）' % (r_new['a_name'], r_old['a_name']))

# 0001 那批的 name 前多一个长度字节，必须剥掉
pref_frame = (bytes([0]) + (1).to_bytes(2, 'little') + b'\x00' + bytes([0x01])
              + bytes([0, 0, 1]) + bytes([4]) + '阿飞'.encode('gbk') + b'\x00')
lay_pref = X.detect_layout([(0, 115, pref_frame)])
check(lay_pref['name_prefixed'] is True, 'name 带 1B 长度前缀 → 识别为 prefixed')
check(X.row_of('x', 0, 115, pref_frame, lay_pref)['a_name'] == '阿飞',
      '长度前缀被剥掉，名字干净')

print()
print('=' * 70)
print(' C. 真实语料（存在则抽查，缺目录则跳过）')
print('=' * 70)
rec_dir = X.mp.DEFAULT_DIR
old = os.path.join(rec_dir, '0021.mwr')
new = os.path.join(rec_dir, '0124.mwr')
if os.path.isdir(rec_dir):
    def rows_of(path):
        out = []
        for i, (_off, op, p) in enumerate(X.decode_frames(open(path, 'rb').read())):
            if op is not None:
                out.append(X.row_of(os.path.basename(path), i, op, p))
        return out
    if os.path.exists(old):
        fam, why = X.classify_file(rows_of(old))
        check(fam == 'legacy', '0021.mwr（10-15 录制）判定为 legacy：%r' % why)
    else:
        print('OK   （跳过）0021.mwr 不存在')
    if os.path.exists(new):
        fam, why = X.classify_file(rows_of(new))
        check(fam == 'current', '0124.mwr（10-18 录制）判定为 current')
        bits = [r['a_bits'] for r in rows_of(new) if r['op'] == 123]
        check(bits and all(X.wstate_bits_valid(b) for b in bits),
              '新族字节全部合法（%r）' % bits)
    else:
        print('OK   （跳过）0124.mwr 不存在')
else:
    print('OK   （跳过）录像目录不存在：%s' % rec_dir)

print()
print('=' * 70)
print(' 录像代际隔离 %s' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
