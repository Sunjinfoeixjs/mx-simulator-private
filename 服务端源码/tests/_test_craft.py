# -*- coding: utf-8 -*-
"""制作链真值 / 活力口径冒烟。

锁死的几件事：
  1. craftdata 产物目录（名称/等级/活力）是客户端真值，48 条全在
  2. 材料 id 全部存在于 itemdata 且名称一致（不是编的 id）
  3. 活力与体力是两个属性，制作扣的是**活力**（下标 24）不是体力（下标 26）
  4. 制作扣活力后，客户端在下一次 PropChange 里能看到新值（否则面板永远显示满活力）
  5. 活力不足 → 拒绝制作，且不扣材料
  6. 制作扣金钱（op104 预览报的 MAKE_COST_MONEY×件数 真扣，2026-10-20）

⚠ 不锁死「材料 → 产物」对应关系：那是模拟参数（客户端与服务端都没有该表），
  见 make_recipes.py 头部「真值边界」说明。
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
for p in (SIM, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import m4_server as m                       # noqa: E402
import make_recipes as mr                   # noqa: E402
from _test_dispatch import make_server, FakeConn, s2c_frames   # noqa: E402

fails = []


def check(name, cond, extra=''):
    print(('  ok   ' if cond else '  FAIL ') + name + ('' if cond else ' → %s' % (extra,)))
    if not cond:
        fails.append(name)


def main():
    print('[1] 产物目录 = 客户端 craftdata 真值')
    check('产物 48 条', len(mr.CRAFT_DATA) == 48, len(mr.CRAFT_DATA))
    check('id 范围 25201..25248',
          sorted(mr.CRAFT_DATA) == list(range(25201, 25249)),
          (min(mr.CRAFT_DATA), max(mr.CRAFT_DATA)))
    lv = [v['lv'] for v in mr.CRAFT_DATA.values()]
    cost = [v['cost'] for v in mr.CRAFT_DATA.values()]
    check('等级 5..120', (min(lv), max(lv)) == (5, 120), (min(lv), max(lv)))
    check('活力 16..58', (min(cost), max(cost)) == (16, 58), (min(cost), max(cost)))
    check('每条都有名称/描述', all(v.get('name') and v.get('desc')
                              for v in mr.CRAFT_DATA.values()))

    print('[2] 材料 id 是 itemdata 里的真实物品')
    import io
    import re
    # 向上找 MX 根（含「客户端源码」的那层）——写死 `..\..` 只在「sim 与 tests 平级」
    # 的旧布局成立，本工作区 sim 在 tests 里，会指到 服务端源码/客户端源码（不存在）。
    _root = os.path.abspath(os.path.join(SIM, '..'))
    while not os.path.isdir(os.path.join(_root, '客户端源码')):
        _up = os.path.dirname(_root)
        if _up == _root:
            break
        _root = _up
    path = os.path.join(_root, '客户端源码', 'src', 'data', 'itemdata.py')
    check('itemdata 存在', os.path.isfile(path), path)
    if os.path.isfile(path):
        src = io.open(path, encoding='utf-8', errors='replace').read()
        missing = [sh for sh, _n, _p in mr.MATERIAL_SHOP
                   if ('%d:' % sh) not in src.split('DATA = {')[1][:4000000]]
        check('12 个材料 id 都能在 itemdata 里找到', not missing, missing)

    print('[3] 活力 / 体力是两个不同属性')
    check('活力下标 != 体力下标',
          m.PROP_IDX_HUOLI != m.PROP_IDX_TILI,
          (m.PROP_IDX_HUOLI, m.PROP_IDX_TILI))
    check('活力下标 == 24（m_Huoli）', m.PROP_IDX_HUOLI == 24, m.PROP_IDX_HUOLI)
    check('体力下标 == 26（m_Tili）', m.PROP_IDX_TILI == 26, m.PROP_IDX_TILI)
    check('两者初值相同但字段独立（这是当年没暴露 bug 的原因）',
          m.HERO_HUOLI_INIT == m.HERO_TILI_INIT == 100,
          (m.HERO_HUOLI_INIT, m.HERO_TILI_INIT))

    print('[4] 制作扣的是活力，不动体力')
    tmp = tempfile.mkdtemp(prefix='m4craft_')
    srv = make_server(tmp)
    # 用真配方：纸张×2 → 花卉剪纸(25201, lv5, cost16)
    srv.hero_id, srv.hero_name, srv.in_war = 1, '测试', False
    paper = srv._grant_item(FakeConn(), 10155, '纸张', 2)
    assert srv.life_lv >= 5, '起始工艺等级应够做 lv5 产物'
    h0, t0 = srv.huoli, srv.tili
    c0 = srv.coins
    conn = FakeConn()
    ok = srv._dispatch(conn, m.C2S_NPC_MAKE, bytes([7]) + struct.pack('<II', paper, paper))
    check('制作成功', ok is True)
    h1, t1 = srv.huoli, srv.tili
    check('活力被扣 %d 点' % (h0 - h1), h0 - h1 == 16, (h0, h1))
    # ★ craft_charge（2026-10-20 实现）：op104 预览报的 100 金/件必须真扣
    check('制作后金币减少（每件 %d 金 × 2 件）' % m.MAKE_COST_MONEY,
          srv.coins == c0 - m.MAKE_COST_MONEY * 2, (c0, srv.coins))
    check('**体力一点没动**（这才是本测试的重点）', t1 == t0, (t0, t1))
    # 扣活力那帧的下标必须是 24
    props = [b for o, b in s2c_frames(conn.buf) if o == m.OP_PROP_CHANGE]
    check('收到 PropChange 帧', len(props) >= 1, len(props))
    check('下发金币 PropChange（下标 %d）' % m.PROP_IDX_GOLD,
          any(len(b) >= 5 and b[0] == m.PROP_IDX_GOLD for b in props),
          [b[0] for b in props])
    check('其中一帧下标 == 24（活力）', any(b[0] == 24 for b in props if len(b) >= 3),
          [b[0] for b in props if len(b) >= 3])
    check('没有下标 == 26（体力）的帧', not any(b[0] == 26 for b in props if len(b) >= 3))

    print('[5] 进世界会同步活力（否则客户端面板永远显示满值）')
    calls = []

    class Rec(FakeConn):
        def sendall(self, data):
            calls.append(data)

    rec = Rec()
    srv._enter_world(rec)
    ops = s2c_frames(b''.join(calls))
    huoli_props = [b for o, b in ops if o == m.OP_PROP_CHANGE
                  and len(b) >= 3 and b[0] == m.PROP_IDX_HUOLI]
    check('进世界下发 m_Huoli', len(huoli_props) == 1, len(huoli_props))
    if huoli_props:
        check('下发的活力值 == 当前值',
              struct.unpack('<H', huoli_props[0][1:3])[0] == srv.huoli,
              (struct.unpack('<H', huoli_props[0][1:3])[0], srv.huoli))

    print('[6] 活力不足 → 拒绝且不扣材料')
    paper2 = srv._grant_item(FakeConn(), 10155, '纸张', 2)
    qty0 = srv.items[paper2]['qty']
    srv.huoli = 0
    conn = FakeConn()
    # 注意：_dispatch 的返回值只表示「这条有没有归某个分支」，
    # 制作成功/失败看的是它自己的返回值和副作用，别混。
    ok = srv._handle_npc_make(conn, bytes([7]) + struct.pack('<II', paper2, paper2))
    check('活力 0 时 _handle_npc_make 返回 False', ok is False, ok)
    check('材料没被扣', srv.items[paper2]['qty'] == qty0,
          (qty0, srv.items[paper2]['qty']))
    check('活力仍是 0', srv.huoli == 0, srv.huoli)
    check('只回对话', [o for o, _ in s2c_frames(conn.buf)] == [m.OP_NPC_CHAT_EXT],
          [o for o, _ in s2c_frames(conn.buf)])

    print('[7] 扣费契约：op104 预览报多少，实扣就是多少')
    # 面板（op155 S2CNpcMakeCost）报的 iMoney 必须 == 实际扣的钱。
    # 否则就是「面板显示 100 金、实际一分不扣」的假承诺（2026-10-20 修复的正是这条）。
    paper3 = srv._grant_item(FakeConn(), 10155, '纸张', 2)
    srv.huoli = 100
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_GETCOST,
                  bytes([7]) + struct.pack('<II', paper3, paper3))
    costs = [b for o, b in s2c_frames(conn.buf) if o == m.S2C_NPC_MAKECOST]
    check('op104 回 S2CNpcMakeCost(155)', len(costs) == 1, len(costs))
    preview = struct.unpack('<H', costs[0][2:4])[0] if costs else -1
    check('预览金额 == %d 金 × 2 件' % m.MAKE_COST_MONEY,
          preview == m.MAKE_COST_MONEY * 2, preview)
    c1 = srv.coins
    h1 = srv.huoli
    q1 = srv.items[paper3]['qty']
    ok = srv._dispatch(FakeConn(), m.C2S_NPC_MAKE,
                       bytes([7]) + struct.pack('<II', paper3, paper3))
    check('制作成功', ok is True, ok)
    check('**实扣 == 预览**（%d 金）' % preview, c1 - srv.coins == preview,
          (preview, c1, srv.coins))
    check('材料 2 件全扣', q1 - srv.items.get(paper3, {'qty': 0})['qty'] == 2,
          (q1, srv.items.get(paper3)))
    check('活力按 craftdata.cost 扣（16 点）', h1 - srv.huoli == 16, (h1, srv.huoli))

    print('[8] 金钱不足 → 拒绝，且材料/活力/金钱一分不动')
    paper4 = srv._grant_item(FakeConn(), 10155, '纸张', 2)
    srv.huoli = 100
    q2 = srv.items[paper4]['qty']
    h2 = srv.huoli
    srv.coins = m.MAKE_COST_MONEY * 2 - 1          # 差 1 金
    c2 = srv.coins
    conn = FakeConn()
    # ★ 用 _handle_npc_make 而不是 _dispatch：后者的 True 只表示「归了某个分支」，
    #   制作成功/失败看的是它自己的返回值（同 [6] 段的注释）。
    ok = srv._handle_npc_make(conn,
                              bytes([7]) + struct.pack('<II', paper4, paper4))
    check('金钱不足 → _handle_npc_make 返回 False', ok is False, ok)
    check('材料没被扣', srv.items[paper4]['qty'] == q2, (q2, srv.items[paper4]['qty']))
    check('活力没被扣', srv.huoli == h2, (h2, srv.huoli))
    check('金钱没被扣', srv.coins == c2, (c2, srv.coins))
    check('只回一条拒绝对话',
          [o for o, _ in s2c_frames(conn.buf)] == [m.OP_NPC_CHAT_EXT],
          [o for o, _ in s2c_frames(conn.buf)])

    print('[9] 工艺熟练度 → 技能等级（长期成长循环，2026-10-22 实现）')
    # 每次成功制作 +1 熟练度；满本级阈值（lv5→6 需 10）则升级、溢出累计。
    # 清空背包再逐轮做，避免容量/残留干扰；每轮重置活力与金钱。
    srv2 = make_server(tmp)
    srv2.life_lv, srv2.life_exp = 5, 0
    for _ in range(10):
        srv2.items = {}                  # 清空背包（不含制作必需）
        pid = srv2._grant_item(FakeConn(), 10155, '纸张', 2)
        srv2.huoli = 100
        srv2.coins = 99999
        srv2._handle_npc_make(FakeConn(), bytes([7]) + struct.pack('<II', pid, pid))
    check('做满 10 件 lv5 产物 → 工艺 5→6 级', srv2.life_lv == 6, srv2.life_lv)
    check('升级后溢出归零（10-10=0）', srv2.life_exp == 0, srv2.life_exp)
    # 升级后，等级门禁随 life_lv 放开：lv5 产物仍可正常制作（gate 是活的，不是卡死的）
    srv2.items = {}
    hpid = srv2._grant_item(FakeConn(), 10155, '纸张', 2)
    srv2.huoli = 100
    srv2.coins = 99999
    ok6 = srv2._handle_npc_make(FakeConn(), bytes([7]) + struct.pack('<II', hpid, hpid))
    check('升级后 lv5 产物仍可正常制作', ok6 is True, ok6)
    # 反向：把 life_lv 压到 1 就应被等级门禁挡下（gate 真生效）
    srv2.life_lv = 1
    srv2.items = {}
    lpid = srv2._grant_item(FakeConn(), 10155, '纸张', 2)
    srv2.huoli = 100
    srv2.coins = 99999
    ok_low = srv2._handle_npc_make(FakeConn(), bytes([7]) + struct.pack('<II', lpid, lpid))
    check('life_lv 压到 1 → 等级门禁挡下', ok_low is False, ok_low)

    print('[10] 活力独立恢复（时间函数，不挂战斗，2026-10-22 改造）')
    srv3 = make_server(tmp)
    srv3.huoli = 50
    got = srv3._regen_huoli(advance=31)     # 推进 31 秒 → 回 1 点
    check('在线 31 秒回 1 点（→51）', got == 1 and srv3.huoli == 51, (got, srv3.huoli))
    got = srv3._regen_huoli(advance=30)     # 再 30 秒 → 再 1 点
    check('再 30 秒再回 1 点（→52）', got == 1 and srv3.huoli == 52, (got, srv3.huoli))
    # 小数位累积：再推进 15 秒不应立刻回（需满 30 秒一跳）
    got = srv3._regen_huoli(advance=15)
    check('未满 30 秒不回点', got == 0 and srv3.huoli == 52, (got, srv3.huoli))
    # 封顶 HERO_HUOLI_MAX
    big = srv3._regen_huoli(advance=10 ** 9)
    check('上限封顶 HERO_HUOLI_MAX', srv3.huoli == m.HERO_HUOLI_MAX
          and big == m.HERO_HUOLI_MAX - 52, (big, srv3.huoli))
    # ★ 关键：活力增长**只**发生在 _regen_huoli 里，战斗胜利**不**重置活力。
    #   源码扫描：self.huoli = HERO_HUOLI_INIT 只能出现在 __init__/读档默认里。
    import io as _io2
    _src = _io2.open(m.__file__, encoding='utf-8', errors='replace').read()
    _resets = [ln for ln in _src.splitlines()
               if 'self.huoli = HERO_HUOLI_INIT' in ln]
    check('活力只在 __init__/读档默认被设为初值（无战斗回满路径）',
          len(_resets) <= 2, _resets)

    print('[11] 炼药入口（op73）：客户端有 UI，但本服无配方数据 → 只识别不实现')
    _dlg = _io2.open(os.path.join(_root, '客户端源码', 'src', 'dialog', 'dlgdrug.py'),
                     encoding='utf-8', errors='replace').read()
    check('客户端有炼药界面 dlgdrug.CDrugDialog', 'CDrugDialog' in _dlg, 'n/a')
    check('客户端炼药子指令：开界面/参悟(C2SDrugDiscover)',
          'C2SOpenDrugUI' in _dlg and 'C2SDrugDiscover' in _dlg, 'n/a')
    _dd = os.path.join(_root, '客户端源码', 'src', 'data', 'drugdata.py')
    check('客户端无 drugdata 配方表（缺材料→产物映射，无法忠实实现）',
          not os.path.isfile(_dd), _dd)
    check("FEATURE_STATUS['alchemy'] == 'not_implemented'",
          m.FEATURE_STATUS.get('alchemy') == 'not_implemented')
    # 服务端：战斗态下发 op73 必须被识别（返回 True，不落进战斗保底）
    srv4 = make_server(tmp)
    srv4.in_war = True
    ret = srv4._dispatch(FakeConn(), m.C2S_PLAYER_DRUG, bytes([1]))   # 子指令 1=参悟
    check('op73 在战斗态被识别（返回 True，未误推进战斗）', ret is True, ret)
    # 确认 dispatch 没有为炼药发任何 S2C 帧（只 _emit 本地记录，不误导客户端）
    _buf = FakeConn()
    srv4._dispatch(_buf, m.C2S_PLAYER_DRUG, bytes([1]))
    check('炼药请求不回任何 S2C 帧（仅本地记录）', len(_buf.buf) == 0, len(_buf.buf))

    print('[12] 背包满 → 拒绝制作（材料/活力/金钱一分不动）')
    srv5 = make_server(tmp)
    srv5.items = {}
    for j in range(19):                  # 19 个占位（不同造型占不同格）
        srv5._grant_item(FakeConn(), 90001 + j, '占位%d' % j, 1)
    pid_paper = srv5._grant_item(FakeConn(), 10155, '纸张', 3)   # 第 20 格（3 件）
    _bag_full = len([it for it in srv5.items.values()
                     if m.ITEM_POS_BAG_START <= it.get('pos', 0) < m.ITEM_POS_BAG_END])
    check('背包已填满 20/20', _bag_full == 20, _bag_full)
    h5, c5, q5 = srv5.huoli, srv5.coins, srv5.items[pid_paper]['qty']
    # 同一摞纸张选 2 件（槽内剩 1 → 不腾格），背包又满 → 必拒
    ok5 = srv5._handle_npc_make(FakeConn(),
                                bytes([7]) + struct.pack('<II', pid_paper, pid_paper))
    check('背包满且材料不腾格 → 拒绝', ok5 is False, ok5)
    check('纸张没扣（仍 3 件）', srv5.items[pid_paper]['qty'] == q5,
          srv5.items[pid_paper]['qty'])
    check('活力没扣', srv5.huoli == h5, srv5.huoli)
    check('金钱没扣', srv5.coins == c5, srv5.coins)

    print('[13] 制作等级门禁：life_lv < 产物等级 → 拒绝（升级前的真实卡点）')
    srv6 = make_server(tmp)
    srv6.life_lv = 1                       # 低于 25201 的 lv5
    pid6 = srv6._grant_item(FakeConn(), 10155, '纸张', 2)
    srv6.huoli = 100
    srv6.coins = 99999
    ok6 = srv6._handle_npc_make(FakeConn(), bytes([7]) + struct.pack('<II', pid6, pid6))
    check('等级不足 → 拒绝', ok6 is False, ok6)
    check('材料没扣', srv6.items[pid6]['qty'] == 2, srv6.items[pid6]['qty'])

    print()
    if fails:
        print('FAIL（%d 项）' % len(fails))
        for f in fails:
            print('  ✗ %s' % f)
        return 1
    print('制作链真值 / 活力口径 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
