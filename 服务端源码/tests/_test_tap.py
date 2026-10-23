# -*- coding: utf-8 -*-
"""可视化调试台数据层回归 —— tests/_test_tap.py

覆盖 tap.py（抓包/分帧/字典解码/丢弃/注入/世界模型）与 debugger.py 的进程内挂载。
**全程零 socket**：用 transport.make_memory_pair() 直接驱动真实 M4Server.handle()。

最强的一条不变量在场景 E：
    「抓包器记下的帧序列 == 客户端实际读到的帧序列」
分帧只要多吞一帧、少吐一帧、串一帧，这条就会红。
"""
import io
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m          # noqa: E402
import tap as T                # noqa: E402
import transport               # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(('OK  ' if cond else 'FAIL') + ' ' + msg)
    ok = ok and bool(cond)
    return cond


# ── 客户端侧最小收发（与 _e2e_m4.py 同源）──
def recv_s2c(c, timeout=2.0):
    c.settimeout(timeout)

    def exact(n):
        buf = b''
        while len(buf) < n:
            ch = c.recv(n - len(buf))
            if not ch:
                raise ConnectionError('closed')
            buf += ch
        return buf
    n = exact(1)[0]
    if n == 0xFF:
        n = struct.unpack('<H', exact(2))[0]
    p = exact(n)
    return p[0], p[1:]


def send_c2s(c, op, body=b''):
    c.sendall(T.make_c2s(op, body))


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def drain(c, timeout=0.35):
    """把当前可读到的 S2C 全读出来（用于「一帧都不该到」的反向断言）。"""
    out = []
    while True:
        try:
            out.append(recv_s2c(c, timeout=timeout)[0])
        except Exception:                                    # noqa: BLE001
            return out


def make_server(tmp, encounter_every=0):
    srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=encounter_every,
                     enemies=3, turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    # ★ 宠物被动（必杀 8% / 连击 40%）会随机改变伤害与帧数：
    #   本文件断言「life==2 的单位仍在模型里且 dead=True」，而真死时会紧跟 WarDel(116)
    #   把单位从表里 pop 掉 —— 一旦随机连击把敌人打死，这条断言就会红。
    #   关掉被动让这一轮固定打不死人（与 _test_battle_flow 同口径）。
    srv.pet_passive_mute = True
    return srv


print('=' * 70)
print(' A. 协议字典与覆盖表装载')
print('=' * 70)
p = T.Protocol()
check(p.ok, '字典加载成功（%s）' % (p.error or 'OK'))
check(len(p.table('C2S')) >= 130, 'C2S 编码表 %d 条' % len(p.table('C2S')))
check(len(p.table('S2C')) >= 160, 'S2C 编码表 %d 条' % len(p.table('S2C')))
ov = p.table('S2C', overlay_only=True)
check(len(ov) >= 35, 'S2C 精确布局覆盖表 %d 条' % len(ov))
check(p.describe('S2C', 96)['src'] == 'overlay', 'op96 SceneM 走精确布局')
check(p.describe('C2S', 54)['name'] == 'C2S_MAP_MOVE', 'op54 名称来自字典')
check(p.describe('S2C', 199)['known'] is False, '未登记编码 known=False')
check(p.describe('C2S', 34)['variants'] >= 2, 'C2S op34 多形态被识别（%d 种）'
      % p.describe('C2S', 34)['variants'])

# ── 分母对账：覆盖率的分母必须取「客户端真的会发哪些编码」，不是字典条数 ──
# 分母小一个，待实现清单就漏一项，使用者就漏做一份工作。
c2s_rows = p.table('C2S')
space = p.c2s_upspace
check(len(space) >= 130,
      '客户端上行常量全集 %d 个（C2S_* + C2GS_*，net.py 权威口径）' % len(space))
check(all(r.get('op') is not None for r in c2s_rows) and len(c2s_rows) >= len(space),
      'C2S 表以客户端常量为分母：表 %d 行 ⊇ 常量 %d 个' % (len(c2s_rows), len(space)))
c2gs = sorted(o for o, n in space.items() if n.startswith('C2GS_'))
check(len(c2gs) >= 10,
      'C2GS_* 那批上行编码被算进分母了（%d 个：%r）——全表清单漏的正是这批'
      % (len(c2gs), c2gs[:8]))
nodef = [r for r in c2s_rows if r['src'] == 'client']
orphan = [r for r in c2s_rows if r['src'] == 'dict']
check(all(r['fields'] == 0 for r in nodef),
      '「客户端会发但字典缺字段定义」单列出来（%d 个）：%r'
      % (len(nodef), [r['op'] for r in nodef]))
check(8 in [r['op'] for r in nodef],
      'op8(C2S_PING) 落在「字典缺定义」而非「服务端未实现」——两个维度确实独立')
check(all(r['op'] not in space for r in orphan),
      '「字典有但客户端找不到常量」单列出来（%d 个）：%r'
      % (len(orphan), [r['op'] for r in orphan]))

# 与人工全表清单交叉：它能反证「全表漏了 C2GS」这件事确实存在
try:
    _full = set()
    with io.open(os.path.join(T.MX_ROOT, '协议资料', '字典',
                              'protocol_c2s_full.txt'), encoding='utf-8') as f:
        for _ln in f:
            _m = re.match(r'\s*(\d+)\s+\S+', _ln)
            if _m and not _ln.lstrip().startswith('#'):
                _full.add(int(_m.group(1)))
    check(len(_full) >= 100 and bool(set(c2gs) - _full),
          '人工全表清单 %d 条，反证它确实漏了 C2GS_*：%r 不在其中'
          % (len(_full), sorted(set(c2gs) - _full)[:6]))
    check(len(_full) < len(space),
          '全表 %d < 客户端常量 %d，所以分母只能取客户端源码'
          % (len(_full), len(space)))
except OSError as e:
    check(False, '读不到人工全表清单做交叉验证：%r' % e)

# stats() 必须把对账结果一并暴露给界面，而不是只给一个百分比
_audit = T.FrameTap(p).stats(include_tables=False)
check(_audit['space']['C2S'] == len(space),
      'stats 暴露分母来源 space.C2S=%d' % _audit['space']['C2S'])
check(sorted(_audit['c2s_nodef']) == sorted(r['op'] for r in nodef)
      and sorted(_audit['c2s_orphan']) == sorted(r['op'] for r in orphan),
      'stats 暴露两侧差异：无字段定义 %r / 幻影 %r'
      % (_audit['c2s_nodef'], _audit['c2s_orphan']))


print()
print('=' * 70)
print(' B. 分帧：任意切分 / 粘连 / 扩展长度 / 半帧保留')
print('=' * 70)
f_s2c = T.make_s2c(96, struct.pack('<5H', 1, 1001, 1001, 45, 40) + '永泽城'.encode('gbk') + b'\x00')
f_c2s = T.make_c2s(54, struct.pack('<HH', 45, 41))
check(f_s2c[0] == len(f_s2c) - 1, 'S2C 首字节 = 含 opcode 的载荷长（%d）' % f_s2c[0])
check(f_c2s[0] == 54 and f_c2s[1] == 4, 'C2S 首字节 = opcode，次字节 = 不含 opcode 的体长')

for d, wire in (('S2C', f_s2c), ('C2S', f_c2s)):
    good = True
    want_op = wire[1] if d == 'S2C' else wire[0]
    for step in (1, 2, 3, 7, len(wire)):
        r = T.Reassembler(d)
        got = []
        for i in range(0, len(wire), step):
            got += r.feed(wire[i:i + step])[0]
        if len(got) != 1 or got[0][0] != want_op:
            good = False
    check(good, '%s 逐字节/分块喂入均恰好还原 1 帧（op=%d）' % (d, want_op))

big = T.make_s2c(96, b'x' * 300)
parts, rest = T.split_frames('S2C', big)
check(len(parts) == 1 and len(parts[0][2]) == 300 and not rest,
      '扩展长度帧（0xFF+u16）还原正确（payload %d，不含 opcode）' % len(parts[0][2]))

s2c_only = f_s2c + T.make_s2c(97, struct.pack('<HH', 46, 41)) + big
parts, rest = T.split_frames('S2C', s2c_only)
check(len(parts) == 3 and rest == b'' and [p[1] for p in parts] == [96, 97, 96],
      '粘连多帧全部切出：%r' % [p[1] for p in parts])
stream = s2c_only + f_c2s[:2]                       # 尾部混入 2B 半帧
parts, rest = T.split_frames('S2C', stream)
check(len(parts) == 3 and rest == f_c2s[:2], '半帧尾巴被完整保留（%dB）' % len(rest))
# 半帧跨 feed：不能丢头，也不能重复吐
r = T.Reassembler('S2C')
got1, _ = r.feed(s2c_only[:-1])              # 最后一帧（big）差 1B
check([x[0] for x in got1] == [96, 97] and r.buf == big[:-1],
      '半帧不产出、整块留在缓冲里：%dB（应为 %dB）' % (len(r.buf), len(big) - 1))
got2, _ = r.feed(s2c_only[-1:])              # 补齐那 1B
check([x[0] for x in got2] == [96] and r.buf == b'',
      '半帧补齐后恰好产出 1 帧，不丢头不重复：%r' % [x[0] for x in got2])

check(T.split_frames('C2S', T.make_c2s(8))[0][0][1:] == (8, b''),
      'C2S 空体帧（PING op8，len=0）合法且还原为 (8, b"")')
check(T.split_frames('C2S', T.make_c2s(163) + T.make_c2s(164))[0] and
      len(T.split_frames('C2S', T.make_c2s(163) + T.make_c2s(164))[0]) == 2,
      'C2S 连续空体帧（防御+逃跑）正确分帧')
check(T.split_frames('C2S', b'') == ([], b'')
      and T.split_frames('S2C', b'') == ([], b''),
      '空数据不产帧、不留垃圾、不进入循环')

# 长度 0 对 S2C 非法：跳过坏头后必须能继续识别紧随其后的合法帧。
bad_then_good = b'\x00' + T.make_s2c(250)
parts, rest = T.split_frames('S2C', bad_then_good)
check([p[1] for p in parts] == [250] and rest == b'',
      'S2C 异常长度 0 被跳过，下一帧未被错误消耗')

# 高频连续流：一次性解析大量最短帧，数量/顺序/余量必须精确，且函数应快速返回。
bulk_c2s = T.make_c2s(8) * 10000
bulk_s2c = T.make_s2c(250) * 10000
bulk_c, rem_c = T.split_frames('C2S', bulk_c2s)
bulk_s, rem_s = T.split_frames('S2C', bulk_s2c)
check(len(bulk_c) == 10000 and not rem_c
      and all(p[1] == 8 for p in bulk_c),
      'C2S 高频连续 10000 帧无丢失/重复/余量')
check(len(bulk_s) == 10000 and not rem_s
      and all(p[1] == 250 for p in bulk_s),
      'S2C 高频连续 10000 帧无丢失/重复/余量')

# 失步自愈：一段「永远等不齐」的垃圾字节不该把分帧器永久卡死
r = T.Reassembler('C2S')
half = b'\x2d\x04\x28'                             # op54 声明 4B 体，只给了 1B
pairs, ds = r.feed(half)
check(pairs == [] and ds == 0 and r.buf == half,
      '半帧先攒着，不会被误判成失步')
# 全 0xFF：C2S 下 op=0xFF、ln=0xFF → 扩展长度 65535，任何对齐都等不齐
r2 = T.Reassembler('C2S')
pairs, ds = r2.feed(b'\xFF' * 5000 + T.make_c2s(8))
check(pairs == [] and ds > 0 and r2.buf == b'',
      '超阈值且无法对齐 → 如实报出跳过的字节数并清空缓冲（跳 %d B）' % ds)
pairs, ds = r2.feed(T.make_c2s(8) + T.make_c2s(54, b'\x2d\x00\x28\x00\x00'))
check([x[0] for x in pairs] == [8, 54], '失步自愈后分帧器照常工作：%r'
      % [x[0] for x in pairs])


print()
print('=' * 70)
print(' C. 覆盖表解码：内核真实 build_* 帧必须「恰好吃满 + 字段名对」')
print('=' * 70)
CASES = [
    (96, m.build_scene_m(1001, '永泽城', 1001, 45, 40), ['scene', 'mapid', 'minimap', 'x', 'y', 'name']),
    (97, m.build_goto_m(46, 41), ['x', 'y']),
    # ★ M9：desc 是 UnpackDesc（不是 C 字符串）；title 仅在 title_len>0 时存在
    (99, m.build_add_npc(1000, 1013, 47, 42, '村长'),
     ['id', 'shape', 'x', 'y', 'block', 'desc', 'direct', 'npctype', 'country', 'addonId',
      'title_len', 'title', 'name']),
    (86, m.build_map_track(1, 45, 40, 1, -1, 0), ['obj_id', 'x', 'y', 'dx', 'dy', 'move_type']),
    # op17 的 value 宽度随 type 变，字段名就是属性名（见 tap._dyn_prop_change）
    (17, m.build_prop_change_hp(400), ['type', 'm_HP']),
    (112, m.build_war_start(1001, 1, 3, 0, 45, 40), ['scene', 'alineup', 'elineup', 'wtype', 'x', 'y']),
    # ★ bf 是位域容器：拆出 allied=bit7 / type=bit6 / child=bit5（客户端 UnpackBit(0/1/2,1)）
    (115, m.build_war_add(1, 101, True, '阿飞'),
     ['id', 'shape', 'desc', 'bf', 'allied', 'type', 'child', 'owner_id', 'cls', 'grade', 'name']),
    (123, m.build_war_wstate(1, 1, 1, 1, -50), ['id', 'hurt_life_ward', 'hp_chg']),
    (72, m.build_task_add(1, '新手任务', '打败毛草团', 1),
     ['tid', 'icon', 'name', 'desc', 'detail_len', 'npc_cnt', 'oid_cnt', 'timelimit',
      'tasktype', 'flag', 't', 'tipss']),
    (152, m.build_npc_chat_ext('村长', '你好', 0, 0), ['ranKey', 'npctype', 'name', 'msg']),
    # ★ op147 的 items 已用 L{} 结构化展开（2026-10-20），解码器会额外产出
    #   逐条展开行 items[0]（供调试台按条查看），故期望里要带上它。
    (147, m.build_npc_buylist(7, [(101, 5, 20)]),
     ['mode', 'ticketid', 'ext', 'items', 'items[0]']),
    # 制作链的两帧：op104 费用应答 / op102 完成通知（都是 2026-10-19 新增的发射体）
    (155, m.build_npc_make_cost(20, 200, 1), ['iTili', 'iMoney', 'sklv']),
    (160, m.build_make_complete(12, 9001, 1, '制造成功：鹿茸 ×1'),
     ['shape', 'id', 'amount', 'desc']),
    (33, m.build_item_add(0, 1, 101, 0, 3, 20),
     ['type', 'id', 'shape', 'special_flag', 'quantity', 'pos']),
    (48, m.build_pet_info(1, '小灵', 101, m.pet_base_dict()),
     ['pid', 'name', 'shape', 'desc', 'grade', 'hp', 'mp', 'fp', 'hpmax', 'mpmax', 'fpmax',
      'damage', 'defense', 'speed', 'magdef', 'str', 'con', 'mag', 'cha', 'dex', 'point',
      'exp', 'expnext', 'expneed', 'life', 'molingguo']),
    # op49 的 n 决定后续 skill_id 条数：_OV_DYN[49] 展开成 sk0..sk(n-1)
    (49, m.build_pet_skill(1, [1103, 1104], 2),
     ['pid', 'n', 'hf_from', 'sk0', 'sk1']),
    # op51 是 20 个具名字段（5 资质 + 更新/进阶 + 9 抗性），不是「resist9」占位
    (51, m.build_pet_prop_view(1, 0, (1, 2, 3, 4, 5), (0,) * 9),
     ['pid', 'extra_zz', 'atk', 'def', 'con', 'magic', 'speed', 'update_flag',
      'update_max', 'promote_zz', 'promote_max', 'resist_lidi', 'resist_lingqiao',
      'resist_qungong', 'resist_putong', 'resist_xian', 'resist_gui', 'resist_yao',
      'resist_zhi', 'resist_nian']),
    # op65 sktype=2（学招式）由 _OV_DYN[65] 展开出 bid/point/cash/iHighLv
    (65, m.build_skill_level_magic(1103, 1),
     ['sktype', 'skid', 'sklevel', 'bid', 'point', 'cash', 'iHighLv']),
    # op65 sktype=1（生活/生产技能=工艺技术）：多出 sklimit/truelv/point/point_max
    (65, m.build_skill_level_life(m.LIFE_SKILL_CRAFT, 5, m.LIFE_LV_MAX, 5, 0,
                                  m.LIFE_EXP_MAX),
     ['sktype', 'skid', 'sklevel', 'sklimit', 'truelv', 'point', 'point_max']),
    (2, m.build_login_account_info(1), ['count', 'now', 'vid']),
    (87, m.build_minerals(1001, [(1, 2, 3)]), ['sub', 'iOpen', 'iscene', 'n', 'entries']),
    (84, m.build_family_members([(1, '甲', 1, 1, 1)]), ['sub', 'body']),
    (89, m.build_minimap_npc(1001, [(3, 4, '村长', '')]), ['sub', 'body']),
]
nofit = []
for op, wire, want in CASES:
    parts, rest = T.split_frames('S2C', wire)
    got_op, body = parts[0][1], parts[0][2]
    if got_op != op:
        nofit.append('op%d 拆出的 opcode 是 %d' % (op, got_op))
        continue
    dec = T.decode_best(p.describe('S2C', op, body)['layouts'], body)
    names = [r['name'] for r in dec['rows']]
    if not dec['fit']:
        nofit.append('op%d 未吃满（tail %dB）' % (op, dec['tail_len']))
    elif names != want:
        nofit.append('op%d 字段名不符：%r' % (op, names))
check(not nofit, '全部 %d 个真实帧：opcode 正确 + 恰好吃满 + 字段名逐项对齐%s'
      % (len(CASES), '' if not nofit else ' —— ' + '；'.join(nofit)))

# ── 字节翻转 fuzz：改 1 字节，解码器不许「静默错位」 ────────────────────
# 背景（2026-10-19）：`decode_fields` 在越界时会把 cur 强推到 total（为了如实展示
# 已读部分），于是**越界帧的 consumed 恰好等于 total、tail 为空**——
# 「吃到一半撞墙」被 `fit` 报成「完美吃满」，只有 note 是证据，而调用方看的是 fit。
# 940 次单字节变异里有 22 次落到这个自相矛盾状态（字符串缺 \0、去 desc 标签越界、
# 长度前缀越界）。修复后 fit 额外要求「无 note、无 tail」。
#
# 不变式（对每个真实帧 × 每个字节 × 两种翻转）：
#   1. 解码不许抛异常；
#   2. consumed 永不超过 payload 长度；
#   3. fit=True ⇒ tail 为空 且 note 为空（三者在语义上必须一致）；
#   4. 长度不变 ⇒ 结构字段数不变（值可以变，那是数据不是错位）。
fuzz_total = 0
fuzz_bad = []
for op, wire, want in CASES:
    _parts, _ = T.split_frames('S2C', wire)
    _body = _parts[0][2]
    for _i in range(len(_body)):
        for _nv in ((_body[_i] ^ 0xFF), (_body[_i] + 1) & 0xFF):
            if _nv == _body[_i]:
                continue
            fuzz_total += 1
            _mb = _body[:_i] + bytes([_nv]) + _body[_i + 1:]
            try:
                _dec = T.decode_best(p.describe('S2C', op, _mb)['layouts'], _mb)
            except Exception as _e:                          # noqa: BLE001
                fuzz_bad.append('op%d off%d 抛异常 %r' % (op, _i, _e))
                continue
            if _dec['fit'] and (_dec['tail_len'] or _dec['note']):
                fuzz_bad.append('op%d off%d fit 但 tail=%dB note=%r'
                                % (op, _i, _dec['tail_len'], _dec['note'][:40]))
check(not fuzz_bad, '字节翻转 fuzz %d 次：无异常、无「fit 却说有问题」的静默错位%s'
      % (fuzz_total, '' if not fuzz_bad else ' —— ' + '；'.join(fuzz_bad[:5])))

# 字段值抽查：真值不因解码而串位
parts, _ = T.split_frames('S2C', m.build_scene_m(1001, '永泽城', 1001, 45, 40))
dec = T.decode_best(p.describe('S2C', 96)['layouts'], parts[0][2])
vals = {r['name']: r['value'] for r in dec['rows']}
check(vals.get('mapid') == 1001 and vals.get('x') == 45 and vals.get('y') == 40
      and vals.get('name') == '永泽城', 'SceneM 字段值抽查：%r' % vals)

parts, _ = T.split_frames('S2C', m.build_war_wstate(1, 1, 1, 1, -50))
dec = T.decode_best(p.describe('S2C', 123)['layouts'], parts[0][2])
d = {r['name']: r['value'] for r in dec['rows']}
check(d.get('id') == 1 and d.get('hurt_life_ward') == 0x15 and d.get('hp_chg') == -50,
      'WarWState 位域/有符号解码：%r（MSB-first：(hurt<<4)|(life<<2)|ward = 0x15；'
      '-50 必须解成负）' % d)

# ★ op17 的 value 宽度随 type 变。曾经写死 4B：每帧都判「越界」，而越界路径
#   fit 仍为 True（不报黄），值恒为 None —— 每场战斗开场 6 帧全中。
bad_pc = []
for idx, val, width, want_name in (
        (16, 400, 2, 'm_HP'),        # TWORD
        (72, 100, 1, 'm_At'),        # TBYTE
        (20, 77, 2, 'm_FP'),         # TWORD
        (43, 50000, 4, 'm_Gold'),    # TDWORD
        (8, 3, 4, 'm_Exp')):
    wire = m.build_prop_change(idx, val, width)
    body = T.split_frames('S2C', wire)[0][0][2]
    dec = T.decode_best(p.describe('S2C', 17, body)['layouts'], body)
    got = {r['name']: r['value'] for r in dec['rows']}
    if not dec['fit'] or got.get(want_name) != val:
        bad_pc.append('#%d %s→%r fit=%s note=%s'
                      % (idx, want_name, got.get(want_name), dec['fit'], dec['note']))
check(not bad_pc, 'op17 按 type 选 value 宽度（1/2/4B 均恰好吃满且值正确）%s'
      % ('' if not bad_pc else ' —— ' + '；'.join(bad_pc)))

# 宽度不可静态确定的类型（未知下标 / 变长 TDESC）→ 退化成「吃掉剩余字节」，
# 既不越界、也不假装知道宽度
for idx in (200, 2):
    body = T.split_frames('S2C', m.build_prop_change(idx, 1, 2))[0][0][2]
    info = p.describe('S2C', 17, body)
    dec = T.decode_best(info['layouts'], body)
    ok = (dec['fit'] and '越界' not in (dec['note'] or '')
          and ('宽度未知' in info['hint'] or '变长' in info['hint']))
    check(ok, 'op17 type=%d 宽度未知时降级为 tail 显示（fit=%r，提示=%r）'
          % (idx, dec['fit'], info['hint'][:40]))

parts, _ = T.split_frames('S2C', m.build_map_track(1, 45, 40, 1, -1, 0))
dec = T.decode_best(p.describe('S2C', 86)['layouts'], parts[0][2])
d = {r['name']: r['value'] for r in dec['rows']}
check(d.get('dx') == 1 and d.get('dy') == -1, 'MapTrack 有符号增量：dx=%r dy=%r' % (d.get('dx'), d.get('dy')))


# ── C3. nethero / netmap 高频帧：按客户端 Unpack 顺序合成 payload，校验线序 ──
# 这些帧服务端大多还不发，但客户端进了城就会收到（别的玩家/NPC 图标/地面物品/特效）。
# 布局一律照 `netmap.py` / `nethero.py` 的 Unpack 调用序列写，`desc` 走标签联合。
def _desc(*tags):
    out = bytes([len(tags)])
    for t, body in tags:
        out += bytes([t]) + body
    return out


SYNTH = [
    ('op22 荣誉', 22, struct.pack('<Bbii', 3, -2, -1500, 5000) + '明教'.encode('gbk') + b'\x00',
     ['rid', 'lv', 'val', 'maxv', 'name']),
    ('op25 银两', 25, struct.pack('<II', 12345, 67890), ['cash', 'cash2']),
    # ★ op26 的 ids 已用 L{count:n} 结构化展开（2026-10-20）：
    #   原来 ids:T 只能给原始块，看不出有几条、每条什么值。
    #   现在会额外产出逐条展开行 ids[0]..ids[n-1]。
    ('op26 进阶心法', 26, struct.pack('<B', 2) + struct.pack('<HH', 1101, 1102),
     ['n', 'ids', 'ids[0]', 'ids[1]']),
    ('op162 物品详情', 162, struct.pack('<BHI', 3, 77, 2101) + b'x\x00',
     ['itype', 'key', 'id', 'ext']),
    ('op190 交易行详情', 190, struct.pack('<BHI', 1, 9, 555) + b'x\x00',
     ['itype', 'key', 'id', 'ext']),
    ('op164 公告(造型二元组)', 164, struct.pack('<BIIII', 6, 1, 2, 3, 4) + '提示'.encode('gbk') + b'\x00',
     ['itype', 'p1', 'p2', 'p3', 'p4', 'msg']),
    ('op164 公告(单造型)', 164, struct.pack('<BI', 2, 4005) + b'x\x00', ['itype', 'shape', 'msg']),
    ('op179 客服应答', 179, struct.pack('<BB', 2, 1) + b'x\x00', ['sub', 'flag', 'answer']),
    ('op85 删对象', 85, struct.pack('<I', 2001), ['id']),
    ('op85 清区域(8B)', 85, struct.pack('<8B', 1, 2, 3, 4, 5, 6, 7, 8),
     ['sx', 'sy', 'ex', 'ey', 'sx1', 'sy1', 'ex1', 'ey1']),
    # 客户端 netmap.S2CMapAddPlayer：country → hideflag → emotetype → s(1B 名字长度)
    # → playername(s) → emotefortitle → title(22) → color → update_flag
    ('op98 玩家进场', 98,
     struct.pack('<IHHH', 100, 1013, 47, 42) + _desc((0, bytes([0x11, 0x22, 0x33])))
     + struct.pack('<BBBB', 0x0B, 0, 3, 3) + b'abc'
     + struct.pack('<B', 0) + b'T' * 22 + struct.pack('<BB', 5, 1),
     ['id', 'shape', 'x', 'y', 'desc', 'country', 'hideflag', 'emotetype', 's',
      'playername', 'emotefortitle', 'title', 'color', 'update_flag']),
    ('op99 带称号的 NPC', 99,
     struct.pack('<IHHHB', 1000, 1013, 47, 42, 1) + _desc() + struct.pack('<BH', 0x21, 1000)
     + struct.pack('<BH', 1, 0) + struct.pack('<B', 4) + '剑客'.encode('gbk') + '村长'.encode('gbk') + b'\x00',
     ['id', 'shape', 'x', 'y', 'block', 'desc', 'direct', 'npctype', 'country',
      'addonId', 'title_len', 'title', 'name']),
    ('op100 地面物品', 100, struct.pack('<IHHHBB', 3001, 10411, 46, 41, 2, 0)
     + '幻灵珠'.encode('gbk') + b'\x00', ['id', 'shape', 'x', 'y', 'color', 'state', 'name']),
    ('op101 NPC 图标', 101, struct.pack('<BIIBB', 1, 1000, 11867, 1, 2),
     ['sub', 'npcId', 'taskId', 'type', 'color']),
    ('op101 NPC 需求', 101, struct.pack('<BIIBI', 2, 1000, 11867, 1, 26001),
     ['sub', 'npcId', 'taskId', 'itype', 'shape']),
    ('op102 特效(目标)', 102, struct.pack('<BIH', 0, 100, 7), ['mode', 'tid', 'eid']),
    ('op102 特效(坐标)', 102, struct.pack('<BHHH', 2, 45, 40, 9), ['mode', 'x', 'y', 'eid']),
    ('op102 飘血', 102, struct.pack('<BIH', 4, 100, 320), ['mode', 'tid', 'hp']),
    ('op102 循环特效', 102, struct.pack('<BHHHBH', 5, 45, 40, 8, 3, 30),
     ['mode', 'x', 'y', 'eid', 'times', 'interval']),
    ('op102 染色', 102, struct.pack('<BIBBB', 6, 100, 1, 2, 3),
     ['mode', 'pid', 'rinc', 'ginc', 'binc']),
    ('op102 天气', 102, struct.pack('<BB', 9, 4), ['mode', 'weather']),
    ('op106 变身', 106, struct.pack('<IH', 100, 2130) + _desc((1, bytes([3, 4])))
     + struct.pack('<BBB', 0x0B, 2, 0) + b'T' * 22 + struct.pack('<B', 9),
     ['id', 'shape', 'desc', 'country', 'emotetype', 'emotefortitle', 'title', 'color']),
    ('op108 隐藏', 108, struct.pack('<I', 100), ['pid']),
    ('op171 临时 NPC', 171, struct.pack('<IH', 500, 4427) + '临时'.encode('gbk') + b'\x00',
     ['id', 'shape', 'name']),
]
bad = []
for label, op, body, want in SYNTH:
    info = p.describe('S2C', op, body)
    dec = T.decode_best(info['layouts'], body)
    names = [r['name'] for r in dec['rows']]
    if not dec['fit'] or names != want:
        bad.append('%s：fit=%s 名=%r 期望=%r %s'
                   % (label, dec['fit'], names, want, dec['note']))
check(not bad, 'nethero/netmap 高频帧 %d 例：恰好吃满 + 字段名逐项对齐%s'
      % (len(SYNTH), '' if not bad else ' —— ' + '；'.join(bad)))

# WarAdd 的位域必须按位拆开，而不是把整字节当 allied（0x80 会被显示成 128）
_ally = T.decode_best(p.describe('S2C', 115, m.build_war_add(100, 1, True, '甲')[2:])['layouts'],
                      m.build_war_add(100, 1, True, '甲')[2:])
_foe = T.decode_best(p.describe('S2C', 115, m.build_war_add(1, 2130, False, '狼')[2:])['layouts'],
                     m.build_war_add(1, 2130, False, '狼')[2:])
_av = {r['name']: r['value'] for r in _ally['rows']}
_fv = {r['name']: r['value'] for r in _foe['rows']}
check(_av.get('allied') == 1 and _fv.get('allied') == 0,
      'WarAdd 位域按位解：友方 allied=1 / 敌方 allied=0（实得 %r / %r）'
      % (_av.get('allied'), _fv.get('allied')))
check(_av.get('bf') == 0x80 and _av.get('type') == 0 and _av.get('child') == 0,
      'bf 保留原始字节 0x80，type/child 各自为 0')

# desc 必须按标签解，而不是当字符串：0 号标签是颜色三元组
_body = struct.pack('<IH', 100, 2130) + _desc((0, bytes([0x11, 0x22, 0x33]))) \
    + struct.pack('<BBB', 0, 0, 0) + b'T' * 22 + struct.pack('<B', 0)
_dec = T.decode_best(p.describe('S2C', 106, _body)['layouts'], _body)
_row = [r for r in _dec['rows'] if r['name'] == 'desc'][0]
check('0=11 22 33' in _row['disp'], 'desc 按标签联合解（%r）' % _row['disp'])

# 同长度不同 mode 必须解出不同字段：0/1-3/4 都是 7B，静态规格会读错
_a = T.decode_best(p.describe('S2C', 102, struct.pack('<BIH', 0, 100, 7))['layouts'],
                   struct.pack('<BIH', 0, 100, 7))
_b = T.decode_best(p.describe('S2C', 102, struct.pack('<BIH', 4, 100, 320))['layouts'],
                   struct.pack('<BIH', 4, 100, 320))
check([r['name'] for r in _a['rows']] != [r['name'] for r in _b['rows']],
      'MapEffect 同样 7B、mode 不同 → 字段不同（%r vs %r）'
      % ([[r['name'] for r in _a['rows']]], [r['name'] for r in _b['rows']]))


print()
print('=' * 70)
print(' D. C2S 多形态试配')
print('=' * 70)
# op34 有 3 种调用形态：C2SItemSummonUse(item_id,summon_id,flag)=9B / C2SItemUse(id,0)=8B
# / C2SItemsUse(*ids)=4B。8B 体必须选「两个 4B」那条，而不是「9B 被截断成 8B」那条。
dec = T.decode_best(p.describe('C2S', 34)['layouts'], struct.pack('<II', 1234, 0))
check(dec['fit'] and dec['layout'] == 1 and [r['value'] for r in dec['rows']] == [1234, 0],
      'op34 8B 体：选中真正的 8B 布局#%d/%d（未被截断的 9B 布局顶替）→ %r'
      % (dec['layout'], dec['n_layouts'], [r['value'] for r in dec['rows']]))
# 16B 体（多 id）字典表达不了「重复最后一个字段」：不许硬报吃满，须留 tail
dec = T.decode_best(p.describe('C2S', 34)['layouts'], struct.pack('<IIII', 1, 2, 3, 4))
check(dec['rows'] and dec['tail_len'] > 0, 'op34 16B 体：如实留下 %dB tail（不假装吃满）'
      % dec['tail_len'])
check(not dec['fit'], '未吃满时 fit=False（界面据此标黄）')
# 越界必须同时写 note：否则截断布局会被误判为「干净吃满」
rows, tail, note, consumed = T.decode_fields([{'kind': 'I', 'size': '9', 'value': 'x'}],
                                             b'\x01\x02')
check(note and consumed == 2, '整数越界同时产出 note 与 consumed：%r' % note)


print()
print('=' * 70)
print(' E. 端到端（零 socket）：抓到的帧序列 == 客户端实际读到的帧序列')
print('=' * 70)
tmp = tempfile.mkdtemp(prefix='m4tap_')
tap = T.FrameTap(p)
check(tap.install(m) is True, 'install() 首次挂载成功')
check(tap.install(m) is False, 'install() 重复调用幂等')

srv = make_server(tmp, encounter_every=3)
srv._persist()
srv2 = make_server(tmp, encounter_every=3)
srv2.have_role = True
srv2.hero_name = '阿飞'
server_end, client_end = transport.make_memory_pair()
th = threading.Thread(target=srv2.handle, args=(server_end,), daemon=True)
th.start()

seen_s2c, seen_c2s = [], []

# ① 握手
op, data = recv_s2c(client_end)
seen_s2c.append(op)
check(op == 1, '服务端先发 Hello(op1)')
client_end.sendall(hello_resp(0x12345678))
time.sleep(0.15)

# ② 登录 → 选角列表
send_c2s(client_end, 1, login_body())
seen_c2s.append(1)
ops = [recv_s2c(client_end)[0] for _ in range(2)]
seen_s2c += ops
check(ops == [2, 3], '登录后收到 AccountInfo(2) + RoleInfo(3)：%r' % ops)

# ③ 选角进世界
send_c2s(client_end, 2, struct.pack('<I', 1))
seen_c2s.append(2)
world_ops = []
t0 = time.time()
while time.time() - t0 < 2.0:
    try:
        world_ops.append(recv_s2c(client_end, timeout=0.4)[0])
    except Exception:                                        # noqa: BLE001
        break
seen_s2c += world_ops
check(16 in world_ops and 96 in world_ops and 99 in world_ops and 97 in world_ops,
      '进世界序列含 PropInit(16)/SceneM(96)/AddNpc(99)/GotoM(97)：%d 帧' % len(world_ops))

# ④ 移动 + NPC（起始图 1001 永泽城 = 城镇）
send_c2s(client_end, 54, struct.pack('<HH', 45, 40) + b'\x00')
seen_c2s.append(54)
seen_s2c += drain(client_end)
check(86 in seen_s2c, '移动后收到 MapTrack(86)')

send_c2s(client_end, 96, struct.pack('<I', 1000))            # 点村长
seen_c2s.append(96)
seen_s2c += drain(client_end)
check(152 in seen_s2c, 'NPC 对话 S2CNpcChatExt(152)')

# 商店列表不是「点一下就发」，要选菜单第 2 项（97 的应答走 98）
send_c2s(client_end, 98, struct.pack('<I', 2))               # 「我想买点东西」
seen_c2s.append(98)
shop = drain(client_end)
seen_s2c += shop
check(147 in shop, '选项 2 → 商店列表 S2CNpcBuyList(147)')

# ④-b 城镇不遇怪（★2026-10-22：暗雷只发生在场景表内的野外/迷宫，
#      城镇/室内不该踩雷——这是原版行为，旧实现「任何图都能遇野狼」是占位实现）
_town_ops = []
for _ in range(3):
    send_c2s(client_end, 54, struct.pack('<HH', 45, 40) + b'\x00')
    seen_c2s.append(54)
    _town_ops += drain(client_end, 0.25)
seen_s2c += _town_ops
check(112 not in _town_ops, '城镇（永泽城）连续移动不触发遇怪')

# ④-c 世界模型（趁还在起始图：断言地图/NPC 名/坐标都被解出来）
#      ★原来放在 E 段末尾，但下面要切到野外图踩雷，切图后地图就不是 1001 了
_stw = tap.stats()['world']
check(_stw['mapid'] == m.START_MAP_ID and _stw['mapname'] == m.MAPS[m.START_MAP_ID]['name'],
      '世界模型：地图 %r(%r)' % (_stw['mapname'], _stw['mapid']))
check(len(_stw['npcs']) >= 2, '世界模型：NPC %d 个' % len(_stw['npcs']))
check(any(n['name'] == '村长' for n in _stw['npcs']), '世界模型：NPC 名字解出「村长」')
check(_stw['x'] == 45 and _stw['y'] == 40,
      '世界模型：坐标 (%r,%r)' % (_stw['x'], _stw['y']))

# ④-d 切到野外图再测踩雷（★必须走真实 C2S：抓包器只包装 handle() 里的 conn，
#      直接调 srv2._goto_map(server_end) 会绕过 TappedConn，破坏 E 段的帧序列不变量）
WILD_MAP = m.MAP_LINKS[m.START_MAP_ID][1]        # 1001 的第 2 个邻接 = 新月平原
check(WILD_MAP in m.ENCOUNTER_BY_MAP, '%s(%d) 在暗雷场景表内'
      % (m.MAPS[WILD_MAP]['name'], WILD_MAP))
send_c2s(client_end, 96, struct.pack('<I', m.TELEPORT_NPC_ID))   # 点传送使
seen_c2s.append(96)
_menu = drain(client_end, 0.3)
seen_s2c += _menu
send_c2s(client_end, 98, struct.pack('<I', 2))                   # 选第 2 个目的地
seen_c2s.append(98)
_scene = drain(client_end, 0.3)
seen_s2c += _scene
check(96 in _scene and 97 in _scene, '切图到 %s 收到 SceneM(96)+GotoM(97)'
      % m.MAPS[WILD_MAP]['name'])
check(srv2.mapid == WILD_MAP, '内核 mapid 已切到 %d（实得 %d）'
      % (WILD_MAP, srv2.mapid))

for _ in range(3):
    send_c2s(client_end, 54, struct.pack('<HH', 45, 40) + b'\x00')
    seen_c2s.append(54)
    seen_s2c += drain(client_end, 0.25)
check(112 in seen_s2c, '野外第 3 次移动触发遇怪 WarStart(112)')

# ⑤ 未知 opcode：内核应记「未识别」，抓包器应把它标为未实现
send_c2s(client_end, 123, b'\x01\x02\x03')
seen_c2s.append(123)
time.sleep(0.25)
seen_s2c += drain(client_end, 0.2)

time.sleep(0.3)
raw = [f for f in tap.frames() if f['op'] == -1]
check(len(raw) == 1 and raw[0]['size'] == 25 and raw[0]['dir'] == 'C2S',
      '握手明文（25B 非帧）被单独记成裸区，未污染分帧器：%r'
      % [(f['dir'], f['size'], f['name']) for f in raw])
check(tap.stats()['desync'] == {'C2S': 0, 'S2C': 0},
      '全程无失步（desync=0，说明裸区标记生效而非靠自愈兜底）')

tap_s2c = [f['op'] for f in tap.frames() if f['dir'] == 'S2C']
tap_c2s = [f['op'] for f in tap.frames() if f['dir'] == 'C2S' and f['op'] != -1]
check(tap_s2c == seen_s2c,
      'S2C 帧序列完全一致（抓包 %d 帧 / 客户端 %d 帧）%s'
      % (len(tap_s2c), len(seen_s2c), '' if tap_s2c == seen_s2c
         else '\n     抓包=%r\n     实收=%r' % (tap_s2c, seen_s2c)))
check(tap_c2s == seen_c2s,
      'C2S 帧序列完全一致（抓包 %d 帧 / 客户端 %d 帧）%s'
      % (len(tap_c2s), len(seen_c2s), '' if tap_c2s == seen_c2s
         else '\n     抓包=%r\n     实发=%r' % (tap_c2s, seen_c2s)))

# ⑥ 载荷逐字节一致（不只是 opcode 序列）
# ★ `hex` 字段只存前 256 字节（调试台防爆），所以长帧只能比对截断后的长度：
#   商店列表（op147）会随商品数变长，超出 256B 是正常的，不是抓包丢数据。
recs_s2c = [f for f in tap.frames() if f['dir'] == 'S2C']
check(all(len(f['hex'].replace(' ', '')) // 2 == min(f['size'], 256) for f in recs_s2c),
      '抓包记录的 payload 长度与实际帧体一致（>256B 按截断比）')

st = tap.stats()
check(123 in st['unknown_c2s'], '内核日志旁听捕获「未识别 op=123」→ %r' % st['unknown_c2s'])
row = [r for r in st['c2s_table'] if r['op'] == 123]
check(row and row[0]['impl'] is False and row[0]['count'] == 1,
      '覆盖表把 op123 标为「服务端未实现」且带出现次数')
check(row and row[0]['src'] == 'none',
      'op123 字典未登记，仍被补进覆盖表（否则最该看的线索反而看不到）')
check(123 in st['undict_c2s'], 'undict_c2s 列出「出现过但字典没登记」的编码：%r'
      % st['undict_c2s'])
check(st['world']['mapid'] == WILD_MAP and st['world']['mapname'] == m.MAPS[WILD_MAP]['name'],
      '世界模型随切图更新到 %r(%r)' % (st['world']['mapname'], st['world']['mapid']))
check(len(st['world']['npcs']) >= 1, '世界模型：野外图 NPC %d 个' % len(st['world']['npcs']))


# ── 战斗模型：真打一个回合，验证 War* 被翻成「谁在场/谁打谁/谁掉多少血」 ──
# 放在帧序列一致性断言之后，免得新增帧把那条强不变量搅乱
print()
print('=' * 70)
print(' E2. 战斗模型（WarStart/WarAdd/WarTurn/WarAttackNormal/WarWState）')
print('=' * 70)
send_c2s(client_end, 160, bytes([1]))         # C2SWarAttack(160)，body[0]=目标 war_id
# ★ 宠物速度(49) 慢于主角(100) 但快于野狼(40)：主角出手后服务端会挂起等宠物指令
#   （op168-174），不补这一帧敌人就永远不会反击，WarWState 只有 1 条。
send_c2s(client_end, 168, bytes([m.PET_WAR_ID, 1]))   # 宠物普攻同一目标
for _ in range(4):                            # 演出帧是分批落的，多收几轮直到静默
    drain(client_end, 0.6)

war = tap.stats()['world']['war']
units = war['units']
ally = [u for u in units.values() if u['allied']]
foe = [u for u in units.values() if not u['allied']]
# 一击可能直接打完（默认敌人数少），所以不硬要求还在战斗中
check(len(units) >= 3,
      '开战后模型建成：在场 %d 个单位（active=%r，回合 %d）'
      % (len(units), war['active'], war['round']))
check(len(ally) == 2 and len(foe) >= 1,
      '阵营划分正确：我方 %r / 敌方 %r'
      % ([u['name'] for u in ally], [u['name'] for u in foe]))
check(war['scene'] is not None and war['alineup'] is not None
      and war['elineup'] is not None,
      'WarStart 字段入库：scene=%r alineup=%r elineup=%r wtype=%r'
      % (war['scene'], war['alineup'], war['elineup'], war['wtype']))
check(war['round'] >= 1, '回合计数已推进到第 %d 回合' % war['round'])

# 模型不许自己算：必须与帧流里 WarWState 的原始 hp_chg 逐一对得上
wf = [f for f in tap.frames() if f['dir'] == 'S2C' and f['op'] == 123]
sum_dmg, deaths = {}, set()
for f in wf:
    d = {r['name']: r['value'] for r in (f['rows'] or [])}
    chg = d.get('hp_chg')
    if isinstance(chg, int) and chg > 0:          # 本服约定：正数=扣血
        sum_dmg[d.get('id')] = sum_dmg.get(d.get('id'), 0) + chg
    if ((d.get('hurt_life_ward') or 0) >> 2) & 0x3 == 2:   # MSB-first：life 在 bit3-2
        deaths.add(d.get('id'))
check(len(wf) >= 2 and len(sum_dmg) >= 1,
      '一个回合内出现 WarWState ×%d，涉及 %d 个单位的扣血：%r'
      % (len(wf), len(sum_dmg), sum_dmg))
bad = {k: (v, units[str(k)]['dmg']) for k, v in sum_dmg.items()
       if str(k) in units and units[str(k)]['dmg'] != v}
check(not bad, '累计扣血与帧流逐单位对得上（不一致 %r）' % bad)
check(all(str(k) in units and units[str(k)]['dead'] for k in deaths),
      'life 位域==2 的单位在模型里都标成死亡（%r）' % sorted(deaths))
check(all(u['hits'] > 0 for u in units.values() if u['dmg'] > 0),
      '受击次数随 WarWState 累加：%r'
      % {u['name']: u['hits'] for u in units.values()})

kinds = [e['kind'] for e in war['log']]
check({'start', 'add', 'turn'} <= set(kinds),
      '时间线按语义归类（%r）' % sorted(set(kinds)))
check(all(isinstance(e['seq'], int) and e['text'] for e in war['log']),
      '每条时间线都带 seq 与可读文案（可点回帧流定位）')
check(any(e['kind'] == 'attack' for e in war['log']),
      '普攻被翻成「A → B」：%r' % [e['text'] for e in war['log'] if e['kind'] == 'attack'][:2])
check(any(e['kind'] == 'wstate' for e in war['log']),
      '结算被翻成可读文案：%r'
      % [e['text'] for e in war['log'] if e['kind'] == 'wstate'][:2])


print()
print('=' * 70)
print(' F/G. 丢帧语义（S2C 客户端收不到 / C2S 内核收不到但连接不断）')
print('=' * 70)
send_c2s(client_end, 8)                       # PING → 服务端回 op250
check(drain(client_end, 1.5) == [250], '基线：PING 应答 250 正常到达')

tap.drop_next('S2C')
send_c2s(client_end, 8)
got = drain(client_end, 0.6)
check(got == [], 'S2C 丢帧：客户端确实没收到这一帧（%r）' % got)
send_c2s(client_end, 8)
got = drain(client_end, 1.5)
check(got == [250], 'S2C 丢帧只影响一帧，链路自动恢复（%r）' % got)

tap.drop_next('C2S')
send_c2s(client_end, 8)                       # 被吞，内核读不到
send_c2s(client_end, 8)                       # 正常
got = drain(client_end, 1.5)
check(got == [250], 'C2S 丢帧：两帧只换来一次应答（内核确实少读一帧）→ %r' % got)
dropped = [f for f in tap.frames() if f['dropped']]
check(len(dropped) == 2 and {f['dir'] for f in dropped} == {'C2S', 'S2C'},
      '丢帧在抓包里留痕（%d 条，方向 %r）' % (len(dropped), {f['dir'] for f in dropped}))
check(tap.stats()['armed'] == {'C2S': 0, 'S2C': 0}, '丢帧指令已消费干净（欠账归零）')

send_c2s(client_end, 8)
check(drain(client_end, 1.5) == [250], 'C2S 丢帧后内核仍能正常读帧（连接未断）')


print()
print('=' * 70)
print(' H/I. 注入语义（S2C 客户端收到 / C2S 内核读到）')
print('=' * 70)
ok_i, note = tap.inject('S2C', 152, struct.pack('<HI', 0, 0) + b'GM'.ljust(20, b'\x00')
                        + '注入测试'.encode('gbk') + b'\x00')
check(ok_i, 'S2C 注入成功：%s' % note)
op, data = recv_s2c(client_end, 1.0)
check(op == 152 and '注入测试'.encode('gbk') in data, '客户端收到注入帧 op=%d' % op)
rec = [f for f in tap.frames() if f['injected'] and f['dir'] == 'S2C']
check(len(rec) == 1 and rec[0]['name'] == 'S2CNpcChatExt',
      '注入的 S2C 也进了抓包且解码正确（%s）' % (rec[0]['name'] if rec else '—'))

before = len([f for f in tap.frames() if f['dir'] == 'C2S'])
ok_c, note_c = tap.inject('C2S', 8)           # 伪造一次 PING，免点 GUI 复现客户端操作
check(ok_c, 'C2S 注入排入成功：%s' % note_c)
got = drain(client_end, 1.5)
check(got == [250], 'C2S 注入被内核当作客户端指令读走并应答 → %r' % got)
c2s_recs = [f for f in tap.frames() if f['dir'] == 'C2S']
check(len(c2s_recs) == before + 1, '注入的 C2S 也进了抓包（%d → %d）'
      % (before, len(c2s_recs)))
check(c2s_recs[-1]['injected'] and c2s_recs[-1]['op'] == 8,
      '注入的 C2S 被标成 injected 且 op 正确')


print()
print('=' * 70)
print(' J. 落地 / 统计完整性')
print('=' * 70)
st = tap.stats()                     # 重新取快照：E 段那份早于丢帧/注入，已过期
out = os.path.join(tmp, 'session.jsonl')
n = tap.export_jsonl(out)
check(n == len(tap.frames()) and os.path.getsize(out) > 0,
      '导出会话 JSONL：%d 帧 / %d B' % (n, os.path.getsize(out)))
import json                                                   # noqa: E402
first = json.loads(open(out, encoding='utf-8').readline())
check({'seq', 'ts', 'dir', 'op', 'name', 'rows', 'hex', 'summary', 'fit'} <= set(first),
      '导出记录字段完整')

for key in ('proto_ok', 'frames', 'seen', 'total_ops', 'dropped', 'armed', 'desync',
            'injected', 'unknown_c2s', 'unrouted_s2c', 'undict_c2s',
            'c2s_table', 's2c_table', 'world'):
    if key not in st:
        check(False, 'stats() 缺字段 %s' % key)
        break
else:
    check(True, 'stats() 字段完整（界面所需 14 项齐备）')

# 覆盖统计与实际抓包一致。
# 口径：op<0 是裸区/失步标记、dropped 是「没到对端」的帧，二者都不计入编码覆盖。
def real_ops(direction):
    return set(x['op'] for x in tap.frames()
               if x['dir'] == direction and x['op'] >= 0 and not x['dropped'])


for direction in ('C2S', 'S2C'):
    got, want = st['seen'][direction], real_ops(direction)
    check(got == len(want),
          'seen.%s 去重计数与实际抓包一致（%d；实际集合 %d 个：%r）'
          % (direction, got, len(want), sorted(want)))
check(st['total_ops']['C2S'] == len(p.table('C2S')), 'total_ops 与字典表长一致')

client_end.close()
time.sleep(0.2)

shutil.rmtree(tmp, ignore_errors=True)
print('OK   已清理测试临时目录')

print('\n=== 调试台数据层回归 %s ===' % ('PASS' if ok else 'FAIL'))
sys.exit(0 if ok else 1)
