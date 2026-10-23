# -*- coding: utf-8 -*-
"""P3-05 经济收口测试 —— 账本 / 服务 / 服务端接线 / 唯一入口纪律。

为什么这三层都要测：
  · 只测账本，无法证明服务端真的在用它（可能只是个没人调的库）
  · 只测服务端数值，无法证明「新写的代码不会再次绕过它」
  · 所以第三层是 **AST 结构断言**：扫描 m4_server.py，任何在允许名单之外
    直接给 self.coins 赋值/增减的代码都要判红。这条比数值断言更难被敷衍 ——
    它检查的是「有没有第二个真相」，不是「这一次算对没有」。

证据等级：机制 = C（本服自建）。原服经济流水语义不可得，客户端也没有账单界面。
价格/奖励**数值**另有出处（world_config.json 的 provenance 段，多为模拟），本测试不碰数值。
"""
import ast
import io
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))          # ← 刻意不复用被测代码的算法
# ★ 内核在 tests/sim（不是 服务端源码/sim —— 那个目录根本不存在）。
#   多个老测试头部写着 sys.path.insert(0, r'…\服务端源码\sim')，那条路径是**空的**，
#   它们能 import 只是因为回归器把 cwd/PYTHONPATH 设成了 SIM。这里按真实位置算。
SIM = os.path.join(HERE, 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import economy_ledger as el
import economy_service as es
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


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(b)


def frames(conn):
    """把 FakeConn 收到的字节流按 S2C 帧切开，返回 [(opcode, payload)]。"""
    out, i, buf = [], 0, b''.join(conn.sent)
    while i < len(buf):
        n = buf[i]
        if n == 0 or i + 1 + n > len(buf):
            break
        out.append((buf[i + 1], buf[i + 2:i + 1 + n]))
        i += 1 + n
    return out


def ops(conn):
    return [op for op, _p in frames(conn)]


def make_server():
    tmp = tempfile.mkdtemp(prefix='p305_')
    s = m.M4Server('127.0.0.1', 3171, 0x1234, 5, enemies=6, turn_timeout=30,
                   logdir=os.path.join(tmp, 'logs'), savedir=os.path.join(tmp, 'save'))
    return s, tmp


class Owner(object):
    """服务测试用的最小宿主：只有三个资源属性，不含任何玩法。"""

    def __init__(self, coins=1000, huoli=10, tili=5):
        self.coins, self.huoli, self.tili = coins, huoli, tili


# ══════════════════════ [1] 账本：五道拒记 ══════════════════════
print('[1] 账本能拒掉自相矛盾的记录')
L = el.EconomyLedger()
check(L.record(el.KIND_COIN, 10.5, 100, 110.5, 'x') is None, '浮点 delta 拒记')
check(L.record(el.KIND_COIN, True, 100, 101, 'x') is None, 'bool 当 int 拒记（True 是 1）')
check(L.record(el.KIND_COIN, 10, 100, 120, 'x') is None,
      'before+delta != after 拒记（100+10≠120）')
check(L.record(el.KIND_COIN, 0, 100, 100, 'x') is None, 'delta==0 拒记（空转不该刷满流水）')
e1 = L.record(el.KIND_COIN, -30, 100, 70, '买药', idem='k1')
check(e1 is not None and e1.seq == 1, '正常记录成功且 seq 从 1 开始', e1)
check(L.record(el.KIND_COIN, -30, 70, 40, '买药', idem='k1') is None,
      '同一 idem 第二次拒记（幂等的账本侧保证）')
check(L.has_idem('k1') and not L.has_idem('k2'), 'has_idem 只认用过的键')

# ══════════════════════ [2] 账本：有界 / 统计不丢 / 淘汰后拒绝对账 ══════════════════════
print('[2] 有界账本：明细会淘汰，但统计不丢、对账会如实说「不可判定」')
L2 = el.EconomyLedger(max_entries=3)
base = 1000
for i in range(10):
    L2.record(el.KIND_COIN, -10, base, base - 10, '第%d笔' % i)
    base -= 10
check(len(L2.entries()) == 3, '明细只留最近 3 条 (got %d)' % len(L2.entries()))
st = L2.stats()
check(st['dropped'] == 7 and st['total'] == 10, '淘汰数与总笔数正确 (got %r)' % (st,))
check(st['kinds'][el.KIND_COIN]['out'] == 100, '累计支出 100（明细分不清也不影响累计）',
      st['kinds'])
check(st['kinds'][el.KIND_COIN]['n_out'] == 10, '累计笔数 10', st['kinds'])
rec, detail = L2.reconcile(el.KIND_COIN, 900)
check(rec is None, '明细被淘汰后对账返回 None（不能假装对上）', detail)
check('淘汰' in detail, '不判定时给出原因：%s' % detail)

L3 = el.EconomyLedger()
L3.record(el.KIND_COIN, -100, 1000, 900, 'a')
L3.record(el.KIND_COIN, +50, 900, 950, 'b')
rec, detail = L3.reconcile(el.KIND_COIN, 950)
check(rec is True, '无淘汰时对账通过：%s' % detail)
rec, detail = L3.reconcile(el.KIND_COIN, 1200)
check(rec is False, '实际值被外部改过时对账失败（这才是对账的意义）：%s' % detail)

# ══════════════════════ [3] 服务：扣/发/不足/幂等 ══════════════════════
print('[3] EconomyService：扣、发、余额不足、幂等')
o = Owner(1000, 10, 5)
sv = es.EconomyService(o, el.EconomyLedger())
r = sv.charge(300, '买剑')
check(r.ok and o.coins == 700, '扣 300 → 1000→700 (got %r)' % (o.coins,), r)
r = sv.charge(5000, '买龙')
check((not r.ok) and r.code == es.R_INSUFFICIENT and o.coins == 700,
      '余额不足 → insufficient 且余额不动 (got %r)' % (o.coins,), r)
r = sv.grant(100, '卖皮')
check(r.ok and o.coins == 800, '发 100 → 700→800', r)
r1 = sv.charge(200, '药A', idem='b1')
r2 = sv.charge(200, '药A', idem='b1')
check(r1.ok and (not r2.ok) and r2.code == es.R_DUPLICATE, '同 idem 只生效一次', (r1, r2))
check(o.coins == 600, '重发没重复扣（800→600 只扣一次）(got %r)' % (o.coins,))
r = sv.charge(-5, '负数')
check((not r.ok) and r.code == es.R_INVALID, '负数金额拒收（方向由方法决定）', r)
r = sv.charge(10, '未知资源', kind='diamond')
check((not r.ok) and r.code == es.R_INVALID, '未知资源类型拒收', r)

# ══════════════════════ [4] 服务：多资源原子（余额不足）══════════════════════
print('[4] charge_multi：一项不够则一项都不扣')
o = Owner(1000, 3, 5)
sv = es.EconomyService(o, el.EconomyLedger())
r = sv.charge_multi([(el.KIND_COIN, 100), (el.KIND_HUOLI, 10)], '制作')
check((not r.ok) and r.code == es.R_INSUFFICIENT, '活力不够 → 整笔拒', r)
check(o.coins == 1000 and o.huoli == 3, '钱和活力都没动（不是「扣了钱没扣活力」）',
      (o.coins, o.huoli))
r = sv.charge_multi([(el.KIND_HUOLI, 3), (el.KIND_COIN, 100)], '制作')
check(r.ok and o.coins == 900 and o.huoli == 0, '都够 → 两项一起扣', (o.coins, o.huoli))
check(len(r.parts) == 2, '多资源结果带 2 个 part', r.parts)

# ══════════════════════ [4b] 服务：多资源原子（账本中途拒记）══════════════════════
print('[4b] charge_multi：账本第二笔失败时，第一笔也不扣')


class HalfLedger(el.EconomyLedger):
    """第 2 次 record 一律失败 —— 模拟账本中途拒记。"""

    def __init__(self):
        el.EconomyLedger.__init__(self)
        self.calls = 0

    def record(self, *a, **kw):
        self.calls += 1
        if self.calls >= 2:
            return None
        return el.EconomyLedger.record(self, *a, **kw)


o = Owner(1000, 100, 5)
sv = es.EconomyService(o, HalfLedger())
r = sv.charge_multi([(el.KIND_COIN, 100), (el.KIND_HUOLI, 10)], '制作')
check((not r.ok) and r.code == es.R_LEDGER_REJECT, '账本拒记 → ledger_reject', r)
check(o.coins == 1000 and o.huoli == 100,
      '整笔未扣：钱没扣、活力没扣（先记账后改值的意义）', (o.coins, o.huoli))

# ══════════════════════ [5] 服务：多资源原子（账本中途拒记）══════════════════════
print('[5] grant_multi：账本第二笔失败时，第一笔也不生效')
o = Owner(1000, 10, 5)
sv = es.EconomyService(o, HalfLedger())
r = sv.grant_multi([(el.KIND_COIN, 100), (el.KIND_HUOLI, 5)], '任务奖励')
check((not r.ok) and r.code == es.R_LEDGER_REJECT, '账本拒记 → ledger_reject', r)
check(o.coins == 1000 and o.huoli == 10,
      '整笔未生效：钱没加、活力没加（原先会「钱加了活力没加」）', (o.coins, o.huoli))

# ══════════════════════ [6] 服务：绝对值（GM）══════════════════════
print('[6] set_absolute：GM 改钱也留流水；改成同一个值不记账')
o = Owner(1000, 10, 5)
sv = es.EconomyService(o, el.EconomyLedger())
r = sv.set_absolute(99999, '[GM]')
check(r.ok and o.coins == 99999 and r.delta == 98999, '设 99999，delta=+98999', r)
n_before = len(sv.ledger.entries())
r = sv.set_absolute(99999, '[GM]')
check(r.ok and r.delta == 0 and len(sv.ledger.entries()) == n_before,
      '设成同一个值：ok 但 delta=0、不新增流水', r)
r = sv.set_absolute(-1, '[GM]')
check((not r.ok) and r.code == es.R_INVALID and o.coins == 99999, '负数目标拒收', r)

# ══════════════════════ [7] 服务端：买 ══════════════════════
print('[7] 服务端 _handle_npc_buy：扣钱 + 一条流水 + 一个事件')
s, tmp = make_server()
try:
    conn = FakeConn()
    c0 = s.coins
    s._handle_npc_buy(conn, 2, 2)          # 幻灵珠 500×2
    check(s.coins == c0 - 1000, '扣钱 %d→%d' % (c0, s.coins))
    tail = s._econ().tail(5, el.KIND_COIN)
    check(len(tail) == 1, '只有 1 条金钱流水（不是 0 也不是 2）(got %d)' % len(tail), tail)
    check(tail and tail[0].delta == -1000 and tail[0].after == s.coins,
          '流水金额与余额一致', tail[0] if tail else None)
    check('NPC 购买' in (tail[0].reason if tail else ''), '流水带得出业务原因',
          tail[0].reason if tail else None)
    gold = [p for op, p in frames(conn) if op == m.OP_PROP_CHANGE and p[0] == m.PROP_IDX_GOLD]
    check(len(gold) == 1, '下发 1 个金钱 PropChange', len(gold))
    check(len(gold) == 1 and len(gold[0]) >= 5,
          '金钱 PropChange 载荷够 4 字节（宽度写错会截断客户端数值）',
          len(gold[0]) if gold else None)
    if gold and len(gold[0]) >= 5:
        check(struct.unpack('<i', gold[0][1:5])[0] == s.coins,
              'PropChange 里的金额 == 服务端余额', gold[0])

    # ══════════════════════ [8] 服务端：钱不够 ══════════════════════
    print('[8] 服务端 _handle_npc_buy：钱不够 → 一分不动、背包不变')
    s.coins = 100
    s._econ_reset()                        # 外部直接改过钱 → 重开账本（否则对账必红）
    s.items = {}
    n0 = len(s.items)
    conn = FakeConn()
    s._handle_npc_buy(conn, 2, 1)
    check(s.coins == 100, '余额仍 100', s.coins)
    check(len(s.items) == n0, '没进背包', (n0, len(s.items)))
    check(len(s._econ().tail(5)) == 0, '没有流水（交易没发生）')
    check(m.OP_ITEM_ADD not in ops(conn), '没下发 ItemAdd')

    # ══════════════════════ [9] 服务端：卖 ══════════════════════
    print('[9] 服务端 _handle_npc_sell：加钱 + 一条流水')
    s.items = {}
    s.coins = 1000
    s._econ_reset()
    iid = s._grant_item(FakeConn(), 10155, '纸张', 3)
    s._send_sell_list(FakeConn())          # 建收购缓存（handler 依赖它）
    conn = FakeConn()
    c1 = s.coins
    body = bytes([1]) + struct.pack('<I', iid)     # flag=1：卖整摞
    s._handle_npc_sell(conn, body)
    tail = s._econ().tail(5, el.KIND_COIN)
    check(s.coins > c1, '卖出后钱变多 %d→%d' % (c1, s.coins))
    check(len(tail) == 1 and tail[0].delta == s.coins - c1, '1 条卖出流水且金额对得上', tail)

    # ══════════════════════ [10] 服务端：制作（多资源原子）══════════════════════
    print('[10] 服务端 _handle_npc_make：钱+活力一次扣；不够时材料也不动')
    s.items = {}
    s.coins = 10000
    s.huoli = 100
    s._econ_reset()
    pid = s._grant_item(FakeConn(), 10155, '纸张', 2)
    h0, c0 = s.huoli, s.coins
    conn = FakeConn()
    okmake = s._handle_npc_make(conn, bytes([7]) + struct.pack('<II', pid, pid))
    check(okmake is True, '制作成功', okmake)
    check(s.coins < c0 and s.huoli < h0, '钱和活力都扣了 (%d→%d, %d→%d)'
          % (c0, s.coins, h0, s.huoli))
    t = s._econ().tail(10)
    kinds = sorted(e.kind for e in t)
    check(kinds == [el.KIND_COIN, el.KIND_HUOLI], '恰好两条流水：金钱 + 活力', kinds)
    check(all(e.delta < 0 for e in t), '两条都是扣', [(e.kind, e.delta) for e in t])

    # 活力不足：材料必须一动不动（扣费排在扣材料之前的意义）
    print('   活力不足 → 材料/钱都不动')
    s.items = {}
    s.coins = 10000
    s._econ_reset()
    pid2 = s._grant_item(FakeConn(), 10155, '纸张', 2)
    q0 = s.items[pid2]['qty']
    s.huoli = 0                            # 直接压到 0（再回一点也远不够 16）
    s._regen_huoli = lambda *a, **k: 0     # 掐掉回活力，保证判定稳定
    c0, n_led = s.coins, len(s._econ().tail(100))
    okmake = s._handle_npc_make(FakeConn(), bytes([7]) + struct.pack('<II', pid2, pid2))
    check(okmake is False, '活力不足 → 返回 False', okmake)
    check(s.items[pid2]['qty'] == q0, '材料没被扣 (%d→%d)' % (q0, s.items[pid2]['qty']))
    check(s.coins == c0, '钱没被扣', (c0, s.coins))
    check(len(s._econ().tail(100)) == n_led, '没有产生流水')

    # ══════════════════════ [11] 服务端：交付 ══════════════════════
    print('[11] 服务端 _handle_npc_give：钱不够时不扣，日志如实说付了 0')
    s, tmp2 = make_server()
    shutil.rmtree(tmp2, ignore_errors=True)
    npcs = s._visible_npc_ids()
    check(len(npcs) > 0, '出生点有可见 NPC（前置条件）', npcs)
    if npcs:
        npc = npcs[0]
        s.coins = 50
        s._econ_reset()
        logs = []
        s.log = lambda msg, _b=logs: _b.append(msg)     # 截日志，别写盘
        c0 = s.coins
        s._handle_npc_give(FakeConn(), struct.pack('<IBI', npc, 1, 999))  # itype=1 宠物，cash=999
        check(s.coins == 50, '钱不够 → 一分不扣 (got %d)' % s.coins)
        check(len(s._econ().tail(5)) == 0, '没有流水')
        check(any('0 金钱' in x for x in logs),
              '日志如实写「0 金钱」——原先打的是请求里的 999，等于撒谎', logs[-1:])
        # 钱够：真扣
        s.coins = 1000
        s._econ_reset()
        s._handle_npc_give(FakeConn(), struct.pack('<IBI', npc, 1, 300))
        check(s.coins == 700, '钱够 → 扣 300 (%d)' % s.coins)
        t = s._econ().tail(5, el.KIND_COIN)
        check(len(t) == 1 and t[0].delta == -300, '1 条交付流水 -300', t)
        check(any('300 金钱' in x for x in logs), '钱够时日志写 300', logs[-1:])

    # ══════════════════════ [12] 服务端：学招式（补漏发的 econ 事件）══════════════════════
    print('[12] 服务端 _hero_learn_magic：扣钱 + 补上前漏发的 econ 事件')
    s, tmp3 = make_server()
    shutil.rmtree(tmp3, ignore_errors=True)
    skid = m.LEARN_MAGIC_IDS[0]
    cost = m.M4Server._learn_cost(1)
    s.coins = 10000
    s._econ_reset()
    s.magics.pop(skid, None)
    conn = FakeConn()
    learned = s._hero_learn_magic(conn, skid)
    check(learned is True, '学招式成功', learned)
    check(s.coins == 10000 - cost, '扣了 %d 金 (%d)' % (cost, s.coins))
    t = s._econ().tail(5, el.KIND_COIN)
    check(len(t) == 1 and t[0].delta == -cost, '1 条学招式流水', t)

    # ══════════════════════ [13] 服务端：交任务幂等 ══════════════════════
    print('[13] 服务端 _turn_in_task：同一任务重复结算只发一次钱')
    s, tmp4 = make_server()
    shutil.rmtree(tmp4, ignore_errors=True)
    s._econ_reset()
    c0 = s.coins
    s._turn_in_task(FakeConn())
    check(s.coins == c0 + m.TASK_REWARD_COINS, '第一次交任务 +%d' % m.TASK_REWARD_COINS)
    c1 = s.coins
    s._turn_in_task(FakeConn())            # 重复结算（模拟上游重复调用）
    check(s.coins == c1, '第二次不再加钱（幂等键挡住）(%d→%d)' % (c1, s.coins))
    dup = [e for e in s._econ().tail(20) if e.idem and e.idem.startswith('turnin:')]
    check(len(dup) == 1, '流水里只有 1 条交任务入账', len(dup))

    # ══════════════════════ [14] 服务端：GM 改钱 ══════════════════════
    print('[14] gm_set_coins：留流水 + 下发 PropChange')
    s, tmp5 = make_server()
    shutil.rmtree(tmp5, ignore_errors=True)
    conn = FakeConn()
    s._conn = conn
    s._econ_reset()
    got = s.gm_set_coins(12345)
    check(got == 12345, '返回值 12345', got)
    t = s._econ().tail(5, el.KIND_COIN)
    check(len(t) == 1 and t[0].after == 12345 and '[GM]' in t[0].reason,
          'GM 改钱留了流水（原先查不到）', t)
    gold = [p for op, p in frames(conn) if op == m.OP_PROP_CHANGE and p[0] == m.PROP_IDX_GOLD]
    check(len(gold) == 1, '下发 1 个金钱 PropChange（原先不发，客户端数字不刷新）', len(gold))
    s._conn = None

    # ══════════════════════ [15] 对账 ══════════════════════
    print('[15] 对账：一轮真实交易后账本能对上')
    s, tmp6 = make_server()
    shutil.rmtree(tmp6, ignore_errors=True)
    s._econ_reset()
    s._handle_npc_buy(FakeConn(), 2, 1)
    s.items = {}
    iid = s._grant_item(FakeConn(), 10155, '纸张', 1)
    s._send_sell_list(FakeConn())
    s._handle_npc_sell(FakeConn(), bytes([1]) + struct.pack('<I', iid))
    rec, detail = s._econ().reconcile(el.KIND_COIN)
    check(rec is True, '金币对账通过：%s' % detail, detail)
    # 绕过服务直接改钱 → 对账必须红
    s.coins += 777
    rec, detail = s._econ().reconcile(el.KIND_COIN)
    check(rec is False, '外部直接改钱 → 对账判红（这才叫护栏）：%s' % detail, detail)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════ [16] 结构：唯一入口纪律 ══════════════════════
print('[16] 结构审计：m4_server 里谁可以直接写 self.coins')
SRC_PATH = os.path.join(SIM, 'm4_server.py')
src = io.open(SRC_PATH, encoding='utf-8').read()
tree = ast.parse(src)

ALLOWED = {'__init__', '_reset_character', '_apply_save'}


def _is_self_coins(node):
    """node 是否是 `self.coins` 这个属性访问。"""
    return (isinstance(node, ast.Attribute) and node.attr == 'coins'
            and isinstance(node.value, ast.Name) and node.value.id == 'self')


writes = []                                     # (函数名, 行号, 形态)
for fn in ast.walk(tree):
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if _is_self_coins(t):
                    writes.append((fn.name, node.lineno, '='))
        elif isinstance(node, ast.AugAssign) and _is_self_coins(node.target):
            writes.append((fn.name, node.lineno, type(node.op).__name__))

outside = [w for w in writes if w[0] not in ALLOWED]
check(len(writes) >= 4, '至少扫到 4 处基线写入（说明 AST 扫描真的在工作，不是空跑）',
      writes)
check(outside == [], '允许名单之外没有任何直接写 self.coins 的代码', outside)
check(len([w for w in writes if w[2] in ('Add', 'Sub')]) == 0,
      '全文件没有 self.coins += / -= （增减一律走服务）',
      [w for w in writes if w[2] in ('Add', 'Sub')])
for name in sorted(ALLOWED):
    check(any(w[0] == name for w in writes), '基线函数 %s 仍在直写（读档/新号语义未变）' % name)

# 服务入口必须真实存在且被调用 —— 防止「定义了没人用」（写了库但业务还在裸改）
EXPECT_CALLS = {'_econ_spend': 3,        # 学招式 / NPC 购买 / 交付
                '_econ_gain': 3,         # NPC 卖出 / 交任务 / 剧情奖励
                '_econ_spend_multi': 1,  # 制作（钱+活力原子）
                '_econ_set': 1}          # GM
for meth, want in sorted(EXPECT_CALLS.items()):
    used = len([n for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr == meth])
    check(used == want, '%s 调用 %d 处（期望 %d）' % (meth, used, want), used)
    check(('def %s(' % meth) in src, '%s 有定义' % meth)

print('')
print('=' * 62)
if ok:
    print('全部通过')
else:
    print('失败 %d 项：' % len(fails))
    for f in fails:
        print('  · ' + f)
print('=' * 62)
sys.exit(0 if ok else 1)
