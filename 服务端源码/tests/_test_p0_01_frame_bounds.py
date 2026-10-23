# -*- coding: utf-8 -*-
"""P0-01 帧边界测试：异常帧 / 截断帧 / 扩展长度帧 / 非法长度 / 连续多帧。

为什么单独开这个文件：
  `_test_tap.py` 的 B 段已经覆盖了「逐字节喂入 / 粘连 / 扩展长度 / 半帧保留 /
  失步自愈」，但**非法长度**这一族只靠代码注释保证（"长度为 0 时跳 1 字节"），
  没有一条断言盯着它。长度字节是分帧器唯一的循环终止条件依据 —— 它一旦算错，
  症状不是「这一帧解错」，而是**整个连接此后永远失步**，且表现为随机乱码、
  极难定位。所以这一族必须单独钉住。

本测试关注的不是「解出的字段对不对」（那是 _test_tap 的活），而是三件事：
  1. **不死循环**：任何输入都要在有限步内返回（长度字节是循环的推进依据）
  2. **不跨帧污染**：一帧坏了，不能把后面完整的帧也吞掉
  3. **半帧不丢头**：字节没到齐时一个字节都不消费（否则下次 feed 会错位）

证据等级：帧格式 = A（真机实测，见 协议资料/规格/M1_协议规格.md）；
本测试造的畸形输入是**本服工程约定**（原服不会发这些帧），验证的是防御行为。
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import tap as T
import m4_server as m

ok = True
fails = []


def check(cond, msg, detail=None):
    global ok
    print(('OK  ' if cond else 'FAIL') + ' ' + msg
          + (('  ← %r' % (detail,)) if (detail is not None and not cond) else ''))
    if not cond:
        ok = False
        fails.append(msg)


def s2c(op, data=b''):
    """手工拼 S2C 帧（绕过 make_s2c 的长度保护，才能造出畸形帧）。"""
    return bytes([len(data) + 1, op]) + data


def ext_s2c(op, data):
    """扩展长度 S2C 帧：0xFF + u16(载荷长) + [op][data]。"""
    payload = bytes([op]) + data
    return b'\xFF' + struct.pack('<H', len(payload)) + payload


def guard(fn, *a):
    """跑一次解析，返回 (结果, 耗时秒)。超时/异常都算失败。"""
    t0 = time.time()
    res = fn(*a)
    return res, time.time() - t0


# ══════════ [1] S2C 非法长度：长度字节为 0 ══════════
print('[1] S2C 长度为 0：必须跳 1 字节重新同步，不能死循环')
# ★ 非法头只能塞**一个** 0x00：S2C 是 [len][op]，多塞一字节的话
#   跳过去之后那个字节会被当成长度 —— 那就不是「重新同步」而是又造了一个坑。
bad = b'\x00' + s2c(96, b'\x01\x02')     # 非法头 + 一帧正常的 op96
(parts, rest), dt = guard(T.split_frames, 'S2C', bad)
check(dt < 2.0, '解析在有限时间内返回（%.3fs）' % dt, dt)
check(len(parts) == 1 and parts[0][1] == 96,
      '非法头之后仍能切出后续正常帧（不跨帧污染）', [(p[1], len(p[2])) for p in parts])
check(rest == b'', '正常帧被完整消费（没留下残字节）', rest)

# 连续 200 个长度 0：每一帧都要跳 1 字节，总步数有界
many_zeros = b'\x00' * 400
(parts, rest), dt = guard(T.split_frames, 'S2C', many_zeros)
check(parts == [] and dt < 2.0, '全 0 流不产帧且不死循环（%.3fs）' % dt, dt)
check(rest == b'', '全 0 流被跳完（未残留）', len(rest))

# ══════════ [2] S2C 扩展长度帧的畸形变体 ══════════
print('[2] S2C 扩展长度（0xFF + u16）：越界/为 0/不足三类畸形')
# 2a 正常扩展帧（对照）：载荷 300B
good_ext = ext_s2c(96, b'x' * 299)
parts, rest = T.split_frames('S2C', good_ext)
check(len(parts) == 1 and parts[0][1] == 96 and len(parts[0][2]) == 299,
      '正常扩展帧还原正确（payload 299）', [(p[1], len(p[2])) for p in parts])

# 2b u16 = 0 → 非法
zero_ext = b'\xFF' + struct.pack('<H', 0) + s2c(97, b'\x01')
parts, rest = T.split_frames('S2C', zero_ext)
check(len(parts) == 1 and parts[0][1] == 97,
      '扩展长度 u16=0 判非法并跳字节重新同步（后面那帧活下来了）',
      [p[1] for p in parts])

# 2c u16 巨大但字节不足 → 半帧，一个字节都不消费
huge = b'\xFF' + struct.pack('<H', 65535) + b'\x60\x01\x02'
parts, rest = T.split_frames('S2C', huge)
check(parts == [] and rest == huge, '扩展长度超出可用字节 → 整段当半帧保留',
      (len(parts), len(rest)))

# 2d 扩展头只到齐一半
(parts, rest), dt = guard(T.split_frames, 'S2C', b'\xFF\x10')
check(parts == [] and dt < 2.0 and rest == b'\xFF\x10',
      '扩展长度头未到齐（只 2B）→ 保留半帧、不死循环', (len(parts), rest))

# ══════════ [3] S2C 截断帧：头声明的长度大于实际字节 ══════════
print('[3] S2C 截断帧：半帧必须原样保留，不得丢头、不得误吞下一帧')
one = s2c(96, b'abcd')
half = one[:-2]                                   # 声明 5B 载荷，只到齐 3B
parts, rest = T.split_frames('S2C', half)
check(parts == [] and rest == half, '截断帧整段保留为半帧', (len(parts), rest))
# 补齐后应恰好产 1 帧（验证「不丢头」）
parts, rest = T.split_frames('S2C', half + one[-2:])
check(len(parts) == 1 and parts[0][1] == 96 and rest == b'',
      '补齐后恰好产出 1 帧且无残留', (len(parts), rest))
# ★ 只差 **1** 字节也必须判半帧（判据写成 `> n` 而不是 `> n-1` 的边界）
#   字节数差 1 时最容易被 off-by-one 放过去，而放过之后整条流就错位了。
one_short = one[:-1]
parts, rest = T.split_frames('S2C', one_short)
check(parts == [] and rest == one_short,
      '只差 1 字节仍判半帧（off-by-one 边界）', (len(parts), rest))
parts, rest = T.split_frames('S2C', one_short + one[-1:])
check(len(parts) == 1 and parts[0][1] == 96 and rest == b'',
      '补上最后 1 字节后恰好产 1 帧', (len(parts), rest))
# 只差 1 字节的扩展长度帧同理
ext = ext_s2c(99, b'cccc')
parts, rest = T.split_frames('S2C', ext[:-1])
check(parts == [] and rest == ext[:-1], '扩展帧只差 1 字节也判半帧', (len(parts), rest))

r = T.Reassembler('S2C')
got, ds = r.feed(half)
got2, ds2 = r.feed(one[-2:])
check(got == [] and len(got2) == 1 and got2[0][0] == 96,
      'Reassembler 跨 feed 拼接：先攒着、补齐后吐 1 帧', (got, got2))

# ══════════ [4] C2S 侧：ln=0 合法、扩展长度、空体帧 ══════════
print('[4] C2S 侧：ln=0 是合法空体帧（与 S2C 语义相反，别搞混）')
parts, rest = T.split_frames('C2S', T.make_c2s(8))
check(len(parts) == 1 and parts[0][1:] == (8, b''),
      'C2S ln=0 → 合法空体帧 (op=8)', [(p[1], p[2]) for p in parts])
# C2S 长度 0 不能被当成非法跳字节：连续两个空体帧要切出 2 帧
two = T.make_c2s(163) + T.make_c2s(164)
parts, rest = T.split_frames('C2S', two)
check([p[1] for p in parts] == [163, 164], 'C2S 连续空体帧切出 2 帧',
      [p[1] for p in parts])
# C2S 扩展长度
big_c2s = T.make_c2s(54, b'y' * 300)
parts, rest = T.split_frames('C2S', big_c2s)
check(len(parts) == 1 and len(parts[0][2]) == 300,
      'C2S 扩展长度帧还原（body 300B）', len(parts))
# C2S 扩展长度头未到齐
parts, rest = T.split_frames('C2S', bytes([54]) + b'\xFF\x01')
check(parts == [] and rest == bytes([54]) + b'\xFF\x01',
      'C2S 扩展长度头未到齐 → 半帧保留', (len(parts), rest))

# ══════════ [5] 连续多帧 + 中途混入畸形：顺序与数量都必须对 ══════════
print('[5] 连续多帧混排畸形帧：合法帧一个都不能少，顺序不能变')
stream = (s2c(96, b'a')            # op96
          + b'\x00'                # 非法头（长度 0，单字节）
          + s2c(97, b'bb')         # op97
          + b'\xFF' + struct.pack('<H', 0)   # 非法扩展头
          + ext_s2c(99, b'ccc')    # op99 扩展帧
          + s2c(100, b''))         # op100 空载荷
(parts, rest), dt = guard(T.split_frames, 'S2C', stream)
check(dt < 2.0, '混排流解析在有限时间内返回（%.3fs）' % dt, dt)
check([p[1] for p in parts] == [96, 97, 99, 100],
      '4 个合法帧全部切出且顺序不变（畸形帧只被跳过）', [p[1] for p in parts])
check(rest == b'', '混排流无残留', rest)
# 逐帧校验载荷没被邻帧污染
check(len(parts) >= 3 and parts[0][2] == b'a' and parts[1][2] == b'bb'
      and parts[2][2] == b'ccc',
      '各帧载荷独立（无跨帧污染）', [p[2] for p in parts])

# ══════════ [6] 未知 opcode 仍要能分帧（异常帧不是畸形帧）══════════
print('[6] 未知 opcode：能分帧、不判死、不吞后续')
unknown = s2c(200, b'\x01\x02\x03') + s2c(96, b'z')
parts, rest = T.split_frames('S2C', unknown)
check([p[1] for p in parts] == [200, 96], '未知 opcode 仍被切出（交给上层报未识别）',
      [p[1] for p in parts])

# ══════════ [7] 内核真帧走一遍边界：长度恰好 254/255/256 ══════════
print('[7] make_s2c 长度阈值（254/255/256）：分帧结果必须与构造一致')
for n in (253, 254, 255, 256, 300):
    wire = m.make_s2c(96, b'q' * (n - 1))      # 载荷 n = 1B opcode + (n-1)B data
    is_ext = wire[0] == 0xFF
    parts, rest = T.split_frames('S2C', wire)
    check(len(parts) == 1 and len(parts[0][2]) == n - 1 and rest == b'',
          '载荷 %dB：扩展=%s，还原 payload %dB' % (n, is_ext, len(parts[0][2])),
          (len(parts), len(parts[0][2]) if parts else None, rest))
    check(is_ext == (n >= 0xFF), '载荷 %dB 的扩展判据与 make_s2c 一致' % n,
          (n, is_ext))

print('')
print('=' * 62)
if ok:
    print('P0-01 帧边界：全部通过')
else:
    print('P0-01 帧边界：失败 %d 项' % len(fails))
    for f in fails:
        print('  · ' + f)
print('=' * 62)
sys.exit(0 if ok else 1)
