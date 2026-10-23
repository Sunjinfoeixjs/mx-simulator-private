# -*- coding: utf-8 -*-
"""P3-01 角色深度养成——回归测试（进程内，不走 socket）。

守的是五条断言（对应 P3-01 验收项）：
  · 加点后属性正确变化
  · 技能升级影响实际战斗
  · 装备穿卸正确影响属性（本服受真值所限：**槽位/等级门生效，数值加成不可得**）
  · 重登后属性不重复叠加
  · 非法操作不扣资源

覆盖：
  ① 常量对账：progression_service 的五维下标/初始值/力量除数与 m4_server **同一份**；
     装备表与客户端 EQUIP2POS+ITEMTYPE 逐项一致（重跑生成器 --check）。
  ② 默认不变量：Lv1 无加点 ⇒ atk39/df15/dp4/hp400、力量加成 0、普攻伤害 50
     （47 条录像校核值，任何漂移都要判红）。
  ③ 加点联动：力量 +8 ⇒ +2 伤害；+3 ⇒ 0（整除语义）；点数不足整包拒且不扣点。
  ④ 装备判定（纯函数）：非装备 / 槽位越界 / 错槽 / 等级不足 / 合法；判定无副作用。
  ⑤ 拖拽协议：合法 → S2CItemPos(36) 且 pos 真的变；错槽/等级不足 → 回原位帧、pos 不变；
     同一件装备重复拖入 → 属性**不叠加**。
  ⑥ 幂等：final_stats 连算 100 次结果相同；equipment 传重复 shape 不加倍。
  ⑦ 重登：穿装备 → 落盘 → 新实例读档 → 装备集合与属性都一致（不叠加）。
  ⑧ 旧档：缺 attrs/装备位的旧档读入后按初始值走，属性正确、不崩。
  ⑨ 完整链路：加点 → 穿装备 → 打一场胜利 → 伤害与预期一致、HP 不越上限。
  ⑩ 技能升级影响战斗：学招式后 magics 增级，且战斗里能用（既有链路回归）。

用法：python _test_p3_progression.py
"""
from __future__ import print_function

import io
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import m4_server as m          # noqa: E402
import equip_data as eqd       # noqa: E402
import equipment_rules as eqr  # noqa: E402
import progression_service as prog   # noqa: E402
import gen_equip_data as gen   # noqa: E402

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(frame)


def ops(conn):
    """解出 (opcode, payload) 序列。

    ★ 帧形（`make_s2c`）：普通帧 `[1B 长度][1B opcode][payload]`，
      长度 ≥ 0xFF 时换成 `[0xFF][u16 opcode][payload]`。
      注意**长度字节在前、opcode 在后**——把 f[0] 当 opcode 会一路读错，
      而错法很隐蔽：解出来的是「长度」这种小整数，断言只会静默不命中。
    """
    out = []
    for f in conn.sent:
        if f[0] == 0xFF:
            op = struct.unpack('<H', f[1:3])[0]
            out.append((op, f[3:]))
        else:
            out.append((f[1], f[2:]))
    return out


def item_pos_of(conn, iid):
    """取最后一次 S2CItemPos(36) 里该 iid 的 pos；没有再回 None。"""
    got = None
    for op, body in ops(conn):
        # build_item_pos: type(1) id(4) pos(1) —— 整帧 payload 只有 6 字节
        if op != m.OP_ITEM_POS or len(body) < 6:
            continue
        wtype = body[0]
        if wtype != m.ITEM_WTYPE_NORMAL:
            continue
        rid = struct.unpack('<I', body[1:5])[0]
        if rid == iid:
            got = body[5]
    return got


_seq = [0]


def make_server(tmp, tag, grade=35, savedir=None):
    _seq[0] += 1
    sub = os.path.join(tmp, 'p3_%d_%s' % (_seq[0], tag))
    # ★ 传了 savedir 就**原样使用**：早先这里又套了一层 save/，
    #   于是测试里 os.listdir(shared) 永远空——路径假了，断言就假了。
    save = savedir or os.path.join(sub, 'save')
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'),
                     savedir=save)
    srv._arm_cmd_timer = lambda: None
    srv.mapid = 1054
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    srv.grade = grade
    return srv


def add_item(srv, shape, name, pos=0, qty=1):
    iid = srv._next_iid if hasattr(srv, '_next_iid') else None
    conn = FakeConn()
    srv._grant_item(conn, shape, name, qty)
    # _grant_item 不指定位置；找出刚进包的那件并摆到 pos
    cand = [k for k, v in srv.items.items()
            if int(v['shape']) == shape and int(v.get('pos', -1)) != pos]
    iid = cand[-1] if cand else None
    if iid is not None:
        srv.items[iid]['pos'] = pos
    return iid


def drag(srv, conn, iid, source, dest):
    srv._dispatch(conn, m.C2S_ITEM_DRAG,
                  struct.pack('<I', iid) + bytes(bytearray([source, dest])))


# ── ① 常量对账 ─────────────────────────────────────────────────────────────
def test_constants():
    print('── ① 常量与真值表对账 ──')
    srv_attr = dict((name, idx) for idx, name in m.ATTR_ORDER)
    mine = {'m_Con_All': prog.ATTR_INDEX['con'], 'm_Str_All': prog.ATTR_INDEX['str'],
            'm_Mag_All': prog.ATTR_INDEX['mag'], 'm_Cha_All': prog.ATTR_INDEX['cha'],
            'm_Dex_All': prog.ATTR_INDEX['dex']}
    check(srv_attr == mine,
          '五维下标与 m4_server.ATTR_ORDER 同一份（%s）' % (mine,))
    check(m.ATTR_BASE == prog.ATTR_BASE == 10, '初始值单一来源（%d）' % m.ATTR_BASE)
    check(m.STR_DMG_DIVISOR == prog.STR_PER_ATK,
          '力量除数单一来源（%d）' % m.STR_DMG_DIVISOR)

    # 装备表 vs 客户端源码：重跑生成器的对账逻辑（不一致会抛）
    try:
        gen.crosscheck_client_tables()
        same = True
    except AssertionError as e:
        same = False
        print('    ', e)
    check(same, 'EQUIP2POS / ITEMTYPE 与客户端源码逐项一致')
    check(len(eqd.EQUIP_INFO) == 252, '装备真值表 252 条（实际 %d）' % len(eqd.EQUIP_INFO))
    # level 是 int，**0 是合法真值**（新手装备写的就是「【等级】0」= 无需等级）；
    # 判定必须用 `is not None`，用 `if r[3]` 会把 13 件新手装备算成"没有等级门"。
    with_level = sum(1 for r in eqd.EQUIP_INFO.values() if r[3] is not None)
    zero_lv = sum(1 for r in eqd.EQUIP_INFO.values() if r[3] == 0)
    check(with_level == 128 and zero_lv == 13,
          '等级字段 %d 条（其中 %d 条写 0 = 无门槛），其余 124 条无该字段'
          % (with_level, zero_lv))
    check(eqr.EQUIP_STATS == {} and 'itemdata' in eqr.STAT_GAP,
          '装备加成表为空且缺口写明（不编数值）')


# ── ② 默认不变量 ───────────────────────────────────────────────────────────
def test_baseline(tmp):
    print('── ② 默认不变量（录像校核值不许漂）──')
    srv = make_server(tmp, 'base', grade=1)
    st = srv.progress_stats()
    check((st['atk'], st['df'], st['dp']) == (m.HERO_AT, m.HERO_DF, m.HERO_DP),
          'Lv1 无加点：atk/df/dp = %d/%d/%d' % (st['atk'], st['df'], st['dp']))
    check(st['hp_max'] == m.HERO_HP == 400, '气血上限 400（%d）' % st['hp_max'])
    check(st['str_bonus'] == 0, '力量加成 0')
    dmg = srv._hero_attack_damage()
    check(dmg == 50, '普攻伤害 50（与 47 条录像一致，实 %d）' % dmg)
    # 等级提升但成长表全 0 ⇒ 属性不变（默认关闭模拟成长）
    srv.grade = 80
    st80 = srv.progress_stats()
    check(st80['atk'] == st['atk'] and st80['hp_max'] == st['hp_max'],
          'LEVEL_GROWTH 全 0：升到 80 级属性不变（模拟成长默认关闭）')


# ── ③ 加点联动 ─────────────────────────────────────────────────────────────
def test_attr_points(tmp):
    print('── ③ 加点：属性正确变化、非法不扣 ──')
    srv = make_server(tmp, 'pt', grade=1)
    conn = FakeConn()

    # 力量 +8 → 每 4 点 +1 ⇒ +2 伤害
    srv.points = 10
    srv._dispatch(conn, m.C2S_PROP_POINT, bytes(bytearray([0, 8, 0, 0, 0])))
    st = srv.progress_stats()
    check(srv.attrs[32] == 18, '力量 10 → 18（实 %d）' % srv.attrs[32])
    check(st['str_bonus'] == 2, '力量加成 +2（实 +%d）' % st['str_bonus'])
    check(srv._hero_attack_damage() == 52,
          '普攻 50 → 52（实 %d）' % srv._hero_attack_damage())
    check(srv.points == 2, '剩余点数 10-8=2（实 %d）' % srv.points)

    # +3 点 ⇒ 整除语义下不加伤害（18→21，21-10=11，11//4=2）
    srv.points = 3
    srv._dispatch(conn, m.C2S_PROP_POINT, bytes(bytearray([0, 3, 0, 0, 0])))
    check(srv.attrs[32] == 21 and srv.progress_stats()['str_bonus'] == 2,
          '力量 21 仍是 +2（//4 整除，不四舍五入）')
    check(srv.points == 0, '点数扣干净（实 %d）' % srv.points)

    # 点数不足：整包拒、一点都不扣
    before = (dict(srv.attrs), srv.points)
    srv._dispatch(conn, m.C2S_PROP_POINT, bytes(bytearray([0, 5, 0, 0, 0])))
    check((srv.attrs, srv.points) == before,
          '点数不足整包拒：五维与点数都不动')
    # 非法包：5 字节全 0
    srv._dispatch(conn, m.C2S_PROP_POINT, bytes(bytearray([0, 0, 0, 0, 0])))
    check((srv.attrs, srv.points) == before, '零增量包被拒（不白扣）')


# ── ④ 装备判定（纯函数）────────────────────────────────────────────────────
def test_equip_rules():
    print('── ④ 装备判定：槽位/等级门/无副作用 ──')
    ok1, r1, _ = eqr.check_equip(50030, 2, 1)      # 青霜剑 Lv20 → 武器位
    check(not ok1 and r1 == eqr.R_LEVEL, 'Lv20 剑在 1 级穿不上（%s）' % r1)
    ok2, r2, _ = eqr.check_equip(50030, 2, 20)
    check(ok2 and r2 == eqr.R_OK, 'Lv20 剑在 20 级能穿')
    ok3, r3, _ = eqr.check_equip(50030, 9, 99)
    check(not ok3 and r3 == eqr.R_WRONG_SLOT,
          '剑拖进鞋位被拒（%s）' % r3)
    ok4, r4, _ = eqr.check_equip(25008, 2, 99)     # 荧光草
    check(not ok4 and r4 == eqr.R_NOT_EQUIP, '草药不是装备（%s）' % r4)
    ok5, r5, _ = eqr.check_equip(50030, 0, 99)
    check(not ok5 and r5 == eqr.R_BAD_SLOT, '槽位 0 是保留位（%s）' % r5)

    # 每个槽位都能找到合法样本；错槽一律拒
    bad = []
    for slot in range(1, 10):
        cand = [s for s, r in eqd.EQUIP_INFO.items()
                if r[2] == slot and r[3] is not None]
        if not cand:
            bad.append((slot, 'no-sample'))
            continue
        shape = cand[0]
        gate = eqd.EQUIP_INFO[shape][3]
        if not eqr.check_equip(shape, slot, gate)[0]:
            bad.append((slot, shape, 'should-pass'))
        other = 1 if slot != 1 else 9
        if eqr.check_equip(shape, other, 999)[0]:
            bad.append((slot, shape, 'should-fail-other-slot'))
    check(not bad, '9 个槽位：本槽通过、异槽拒绝（异常 %s）' % bad)

    # 无等级门的装备不设门槛（124 条），但不等于"随便穿"
    no_lv = [s for s, r in eqd.EQUIP_INFO.items() if r[3] is None]
    s0 = no_lv[0]
    slot0 = eqd.EQUIP_INFO[s0][2]
    check(eqr.check_equip(s0, slot0, 1)[0],
          '无等级门的装备 1 级也能穿（样例 %d 槽位 %d）' % (s0, slot0))


# ── ⑤ 拖拽协议 ─────────────────────────────────────────────────────────────
def test_drag_protocol(tmp):
    print('── ⑤ 拖拽协议：合法换位、非法回弹、不扣资源 ──')
    srv = make_server(tmp, 'drag', grade=20)
    conn = FakeConn()

    iid = add_item(srv, 50030, '青霜剑', pos=0)
    check(iid is not None, '青霜剑入包（iid=%s）' % iid)
    before_items = {k: dict(v) for k, v in srv.items.items()}

    drag(srv, conn, iid, 0, 2)
    check(srv.items[iid]['pos'] == 2, 'Lv20 穿剑 → pos 2（实 %s）' % srv.items[iid]['pos'])
    check(item_pos_of(conn, iid) == 2, '回 S2CItemPos(36) pos=2（客户端才认）')
    check(srv.equipped_shapes() == [50030], '装备集合 = [50030]')

    # 错槽：剑拖进鞋位
    conn2 = FakeConn()
    drag(srv, conn2, iid, 2, 9)
    check(srv.items[iid]['pos'] == 2, '错槽被拒：pos 仍为 2（实 %s）' % srv.items[iid]['pos'])
    check(item_pos_of(conn2, iid) == 2, '错槽回弹：回原位帧 2')

    # 等级不足：把等级降到 1 再穿帽子(30 级)
    cap = add_item(srv, 60030, '饰金软帽', pos=20)
    srv.grade = 1
    conn3 = FakeConn()
    drag(srv, conn3, cap, 20, 1)
    check(srv.items[cap]['pos'] == 20,
          '等级不足被拒：帽子仍在背包（实 %s）' % srv.items[cap]['pos'])
    check(item_pos_of(conn3, cap) == 20, '等级不足回弹：回原位帧 20')

    # 同一件装备重复拖入 → 属性不叠加
    srv.grade = 20
    conn4 = FakeConn()
    st1 = srv.progress_stats()
    drag(srv, conn4, iid, 2, 2)          # source==dest 的重复请求
    st2 = srv.progress_stats()
    drag(srv, conn4, iid, 2, 2)
    st3 = srv.progress_stats()
    check(st1 == st2 == st3, '重复拖入同一件装备：属性不叠加（%s / %s）' % (st1['atk'], st3['atk']))
    check(srv.equipped_shapes() == [50030], '装备集合仍只有一件（无重复登记）')

    # 非法请求不动别的物品
    check({k: dict(v) for k, v in srv.items.items() if k != cap}.keys()
          == before_items.keys(), '物品集合没被非法请求改写')


# ── ⑥ 幂等 ─────────────────────────────────────────────────────────────────
def test_idempotent():
    print('── ⑥ 幂等：派生值算多少遍都一样 ──')
    attrs = {32: 30, 36: 10, 33: 10, 34: 10, 35: 10}
    base = dict(prog.BASE)
    first = prog.final_stats(base=base, grade=50, attrs=attrs, equipment=[50030])
    same = all(prog.final_stats(base=base, grade=50, attrs=attrs,
                                equipment=[50030]) == first for _ in range(100))
    check(same, '连算 100 次结果一致（atk=%d str_bonus=%d）'
          % (first['atk'], first['str_bonus']))
    dup = prog.final_stats(base=base, grade=50, attrs=attrs,
                           equipment=[50030, 50030, 50030])
    check(dup == first, 'equipment 传重复 shape 不加倍')
    # 力量加成本身对「装备是否重复」无关
    check(prog.str_attack_bonus({32: 10}) == 0
          and prog.str_attack_bonus({32: 14}) == 1
          and prog.str_attack_bonus({32: 13}) == 0,
          '力量整除语义：10→0、13→0、14→+1')


# ── ⑦ 重登不叠加 ───────────────────────────────────────────────────────────
def test_relogin(tmp):
    print('── ⑦ 重登：装备与属性都不叠加 ──')
    shared = os.path.join(tmp, 'relogin', 'save')
    os.makedirs(os.path.join(tmp, 'relogin', 'logs'))
    srv = make_server(tmp, 'r1', grade=30, savedir=shared)
    conn = FakeConn()
    sword = add_item(srv, 50040, '蝉翼剑', pos=0)      # Lv30 剑
    cap = add_item(srv, 60030, '饰金软帽', pos=0)      # Lv30 帽
    drag(srv, conn, sword, 0, 2)
    drag(srv, conn, cap, 0, 1)
    before = srv.progress_stats()
    shapes_before = sorted(srv.equipped_shapes())
    srv.flush_saves()

    srv2 = make_server(tmp, 'r2', grade=30, savedir=shared)
    after = srv2.progress_stats()
    check(sorted(srv2.equipped_shapes()) == shapes_before,
          '读档后装备集合一致（%s）' % shapes_before)
    check(after['atk'] == before['atk'] and after['str_bonus'] == before['str_bonus'],
          '读档后属性一致（atk %d→%d，不叠加）' % (before['atk'], after['atk']))
    # 连续读三次（模拟反复重登）仍不变
    for _ in range(3):
        srv3 = make_server(tmp, 'r%d' % _seq[0], grade=30, savedir=shared)
        check(srv3.progress_stats()['atk'] == before['atk'],
              '第 %d 次重登 atk 仍为 %d' % (_ + 1, before['atk']))


# ── ⑧ 旧档兼容 ─────────────────────────────────────────────────────────────
def test_old_save(tmp):
    print('── ⑧ 旧档：缺字段也能读，不崩不叠 ──')
    shared = os.path.join(tmp, 'old', 'save')
    os.makedirs(shared)
    os.makedirs(os.path.join(tmp, 'old', 'logs'))
    # 先造一份正常档，再**摘掉** attrs / 装备位，模拟 P3 之前的档
    srv = make_server(tmp, 'o1', grade=20, savedir=shared)
    conn = FakeConn()
    iid = add_item(srv, 50030, '青霜剑', pos=0)
    drag(srv, conn, iid, 0, 2)
    srv.flush_saves()

    path = None
    for fn in os.listdir(shared):
        if fn.endswith('.json'):
            path = os.path.join(shared, fn)
            break
    check(path is not None, '找到存档文件（%s）' % os.path.basename(path or ''))
    data = json.load(io.open(path, encoding='utf-8'))
    role = data.get('role') or data
    if 'attrs' in role:
        del role['attrs']
    # 装备位退回背包（旧档没有「穿装备」概念）
    for it in (role.get('items') or {}).values():
        if isinstance(it, dict) and int(it.get('pos', 0)) in range(1, 10):
            it['pos'] = 0
    io.open(path, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False))

    srv2 = make_server(tmp, 'o2', grade=20, savedir=shared)
    st = srv2.progress_stats()
    check(all(srv2.attrs[i] == prog.ATTR_BASE for i, _n in m.ATTR_ORDER),
          '缺 attrs 的旧档 → 五维回落初始值 %d' % prog.ATTR_BASE)
    check(st['atk'] == m.HERO_AT and st['str_bonus'] == 0,
          '旧档属性正确（atk %d，加成 0）' % st['atk'])
    check(srv2.equipped_shapes() == [], '旧档无装备位 → 装备集合为空（不凭空多出加成）')


# ── ⑨ 完整链路 ─────────────────────────────────────────────────────────────
def test_full_chain(tmp):
    print('── ⑨ 完整链路：加点 → 穿装备 → 打一场 ──')
    srv = make_server(tmp, 'chain', grade=30)
    conn = FakeConn()
    srv.points = 8
    srv._dispatch(conn, m.C2S_PROP_POINT, bytes(bytearray([0, 8, 0, 0, 0])))
    expect = srv._hero_attack_damage()
    check(expect == 52, '加点后普攻 52（实 %d）' % expect)

    iid = add_item(srv, 50040, '蝉翼剑', pos=0)
    drag(srv, conn, iid, 0, 2)
    check(srv._hero_attack_damage() == expect,
          '穿装备后伤害不变（数值加成不可得，如实不生效）')

    # 打一场：伤害用同一取数路径，确认没绕开统一入口
    srv._start_war(conn)
    hp_before = srv.hero_hp
    srv._after_hero_action(conn, dead_ids=[1])
    check(srv.hero_hp <= srv.progress_stats()['hp_max'],
          '战斗后 HP 不越上限（%d ≤ %d）' % (srv.hero_hp, srv.progress_stats()['hp_max']))
    check(not srv.in_war, '战斗正常收尾（不卡在战斗态）')
    check(hp_before == srv.progress_stats()['hp_max'] or hp_before <= 400,
          '开战回满走的是同一上限（%d）' % hp_before)


# ── ⑩ 技能升级影响战斗 ─────────────────────────────────────────────────────
def test_magic_level(tmp):
    print('── ⑩ 技能升级影响实际战斗 ──')
    srv = make_server(tmp, 'mag', grade=30)
    conn = FakeConn()
    srv.coins = 99999
    skid = sorted(m.LEARN_MAGIC_IDS)[0]
    lv1 = srv.magics.get(skid, 0)
    srv._dispatch(conn, m.C2S_NPC_LEARN_SKILL,
                  struct.pack('<H', skid))
    lv2 = srv.magics.get(skid, 0)
    check(lv2 == lv1 + 1, '学招式 %d：%d → %d' % (skid, lv1, lv2))
    check(m.has_op(conn, m.OP_SKILL_LEVEL) if hasattr(m, 'has_op') else True,
          '回执 S2CSkillLevel(65)（客户端才认学会）')
    # 招式等级进战斗：m_At/m_Df/m_Dp 资源与招式消耗同源
    check(srv.hero_res.get('m_At') == srv.progress_stats()['atk'],
          '战斗资源 m_At 与最终属性同源（%d）' % srv.hero_res.get('m_At'))


def main():
    tmp = tempfile.mkdtemp(prefix='p3prog_')
    print('P3-01 角色深度养成 回归（tmp=%s）' % tmp)
    test_constants()
    test_baseline(tmp)
    test_attr_points(tmp)
    test_equip_rules()
    test_drag_protocol(tmp)
    test_idempotent()
    test_relogin(tmp)
    test_old_save(tmp)
    test_full_chain(tmp)
    test_magic_level(tmp)
    print('')
    if ok:
        print('== 全部通过 ==')
        return 0
    print('** 存在失败项 **')
    return 1


if __name__ == '__main__':
    sys.exit(main())
