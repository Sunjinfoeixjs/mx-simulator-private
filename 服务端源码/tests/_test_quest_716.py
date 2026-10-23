# -*- coding: utf-8 -*-
"""主线 7–16 与「17–22 不可得」——回归测试（进程内，不走 socket）。

覆盖：
  ① 数据自洽：16 条主线的编号/步数/等级/剧情点与一览表逐条对齐；每步 NPC 都有摆位、
     地图都存在；剧情物品 shape 能在客户端 itemdata 精确命中；每处战斗都标 shape_sim 且带坐标。
  ② 坐标双源：标 npcposdata 的摆位必须与客户端 npcposdata 同图同坐标；
     标 quest-spawn 的必须确实是「世界表里没有这个人」；场景别名（清风村/珊瑚迷宫二层）落点正确。
  ③ 接取门：Lv34 接不到 35 级的 107、Lv35 能接；115 缺前置被拒、补全后能接；
     等级上限必须 ≥ 最高真值门槛（80）。
  ④ 全链路 107（16 步）：2 场战斗 + 1 处交物品 + 1 个降级步，剧情点 1+1+1 = 3。
  ⑤ 降级步：刃虎宝宝 / 大螃蟹宝宝在本版 itemdata 查无 → 该步是 talk 且带 note，不卡流程。
  ⑥ 任务态可见性：同一张图的两个剧情 NPC 只显示当前步骤要交互的那个；世界 NPC 常显。
  ⑦ 17–22：登记 6 条，各有 blocked/missing_npcs，且缺的名字在本服一个都摆不出来。
  ⑧ 落盘往返：完成 107 后存档 → 新实例读档 → quests_done 与剧情点保留。

用法：python _test_quest_716.py
"""
from __future__ import print_function

import ast
import io
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


def _mx_root(start):
    d = os.path.abspath(start)
    for _ in range(6):
        if os.path.isdir(os.path.join(d, '客户端源码')):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


MX = _mx_root(HERE)
CLIENT_SRC = os.path.join(MX, '客户端源码', 'src')
import m4_server as m          # noqa: E402
import quest_data as qd        # noqa: E402

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


def has_op(conn, op):
    for f in conn.sent:
        n = f[0]
        body = f[1:]
        if n == 0xFF:
            n = struct.unpack('<H', f[1:3])[0]
            body = f[3:]
        if body and body[0] == op:
            return True
    return False


_seq = [0]


def make_server(tmp, tag, mapid=1054, grade=35, savedir=None):
    _seq[0] += 1
    sub = savedir or os.path.join(tmp, 'case%d_%s' % (_seq[0], tag))
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'),
                     savedir=os.path.join(sub, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv.mapid = mapid
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    srv.grade = grade
    return srv


NPCID = {(mid, name): nid for (mid, nid, _sh, _x, _y, name, _src)
         in qd.NPC_SPAWNS}
SRC = {(mid, name): src for (mid, nid, _sh, _x, _y, name, src)
       in qd.NPC_SPAWNS}

# 一览表（quests_mainline.json）7–16 的 (no, 步数, 等级, 剧情点)
EXPECT = {
    107: (7, 16, 35, 3), 108: (8, 13, 40, 3), 109: (9, 16, 45, 3),
    110: (10, 13, 50, 3), 111: (11, 11, 55, 3), 112: (12, 15, 60, 3),
    113: (13, 16, 65, 3), 114: (14, 16, 70, 3), 115: (15, 5, 75, 1),
    116: (16, 13, 80, 2),
}


def look(srv, conn, npcid):
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', npcid))


def answer(srv, conn, choice=1):
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', choice))


def goto(srv, mapid):
    srv.mapid = mapid
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)


def accept(srv, conn, qid):
    q = qd.quest(qid)
    goto(srv, q['start_map'])
    look(srv, conn, NPCID[(q['start_map'], q['start_npc'])])
    answer(srv, conn, 1)


def do_step(srv, conn, qid, st):
    """按步骤类型走一步：交物品先补货；战斗用一击致死收尾。"""
    goto(srv, st['map'])
    if st['kind'] == 'item':
        shape, name = qd.ITEM[st['item']]
        srv._grant_item(conn, shape, name, int(st.get('qty', 1)))
    look(srv, conn, NPCID[(st['map'], st['npc'])])
    answer(srv, conn, 1)
    if st['kind'] == 'fight':
        srv._after_hero_action(conn, dead_ids=[1])


# ── ① 数据自洽 ─────────────────────────────────────────────────────────────
def test_data():
    print('── ① 数据自洽（7–16 与一览表对齐）──')
    check(len(qd.QUESTS) == 16, '主线 16 条（实际 %d）' % len(qd.QUESTS))
    got = {qid: (q['no'], len(q['steps']), q['level'], q['point'])
           for qid, q in qd.QUESTS.items()}
    miss = {k: v for k, v in EXPECT.items() if got.get(k) != v}
    check(not miss, '编号/步数/等级/剧情点 与一览表一致（不符 %s）' % miss)

    bad = []
    for qid, q in qd.QUESTS.items():
        total = sum(int((st.get('reward') or {}).get('point') or 0)
                    for st in q['steps'])
        if total != q['point']:
            bad.append((qid, total, q['point']))
    check(not bad, '剧情点合计 = 一览表（不等 %s）' % bad)
    tot = sum(q['point'] for qid, q in qd.QUESTS.items() if qid >= 107)
    check(tot == 27, '7–16 剧情点合计 27（实际 %d）' % tot)

    bad_step = []
    for qid, q in qd.QUESTS.items():
        if (q['start_map'], q['start_npc']) not in NPCID:
            bad_step.append(('start', qid, q['start_npc']))
        for i, st in enumerate(q['steps']):
            if (st['map'], st['npc']) not in NPCID:
                bad_step.append((qid, i, st['npc']))
            if st['map'] not in m.MAPS:
                bad_step.append((qid, i, 'map', st['map']))
    check(not bad_step, '每步 NPC 都有摆位、地图都存在（缺 %s）' % bad_step[:6])

    # 客户端 itemdata 精确比对（不静默替换）
    src = io.open(os.path.join(CLIENT_SRC, 'data', 'itemdata.py'),
                  encoding='utf-8', errors='ignore').read()
    st0 = src.find('{', src.find('DATA = {'))
    depth, quote, esc, j = 0, None, False, st0
    while j < len(src):
        c = src[j]
        if quote:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == quote:
                quote = None
        else:
            if c in ('"', "'"):
                quote = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    break
        j += 1
    data = ast.literal_eval(src[st0:j + 1])
    names = {}
    for shape, row in data.items():
        nm = row[0]
        names[int(shape)] = nm.decode('gbk', 'replace') if isinstance(nm, bytes) else nm
    bad_item = [(k, s, n, names.get(s)) for k, (s, n) in qd.ITEM.items()
                if names.get(s) != n]
    check(not bad_item, '剧情物品 shape/名称与 itemdata 一致（漂移 %s）' % bad_item)

    fights = [(qid, i) for qid, q in qd.QUESTS.items()
              for i, st in enumerate(q['steps']) if st['kind'] == 'fight']
    unmarked = [(qid, i) for (qid, i) in fights
                if not qd.QUESTS[qid]['steps'][i].get('shape_sim')]
    check(len(fights) == 49 and not unmarked,
          '战斗步骤 %d 处（1–16 合计），全部标 shape_sim（漏标 %s）'
          % (len(fights), unmarked))
    bad_fight = [(qid, i) for (qid, i) in fights
                 if 'x' not in qd.QUESTS[qid]['steps'][i]
                 or 'shape' not in qd.QUESTS[qid]['steps'][i]]
    check(not bad_fight, '战斗步骤都带坐标与造型（缺 %s）' % bad_fight)

    # item 步必须能查到目标（否则 _quest_item_count 永远为 0 → 卡死）。
    # 1–3 里「30级武器」走 any_shapes（多 shape 任选其一），不算缺。
    bad_item_step = [(qid, i, st['item']) for qid, q in qd.QUESTS.items()
                     for i, st in enumerate(q['steps'])
                     if st['kind'] == 'item' and not st.get('any_shapes')
                     and st['item'] not in qd.ITEM]
    check(not bad_item_step, '交物品步的 item 都在 ITEM 表（缺 %s）' % bad_item_step)

    # 前置只用 qid（引擎按 qid 比 quests_done），且只有 115 有前置
    with_prev = {qid: q.get('prev') for qid, q in qd.QUESTS.items()
                 if q.get('prev')}
    check(set(with_prev) == {115} and with_prev[115] == (110, 111, 112, 113, 114),
          '只有 115 有前置，且是 qid 元组（实际 %s）' % with_prev)


# ── ② 坐标双源 ─────────────────────────────────────────────────────────────
def test_placement():
    print('── ② 坐标双源（npcposdata / 资料坐标表）──')
    sys.path.insert(0, MX)
    from _find_npc_pos import load as load_npcpos
    pos = {}
    for mapid, rows in load_npcpos().items():
        for r in rows:
            nm = (r[0].decode('gbk', 'replace') if isinstance(r[0], bytes)
                  else str(r[0]))
            pos.setdefault(nm, []).append((mapid, r[4]))

    bad = []
    for (mid, nid, sh, x, y, nm, src) in qd.NPC_SPAWNS:
        if not str(src).startswith('npcposdata'):
            continue
        hit = [(v[0], v[1]) for v in pos.get(nm) or () if v[0] == mid and v[1]]
        if (x, y) not in [h[1] for h in hit]:
            bad.append((mid, nm, (x, y), hit))
        if sh != nid:
            bad.append((mid, nm, 'shape≠npcid', (sh, nid)))
    check(not bad, '标 npcposdata 的摆位与客户端世界表同图同坐标（不符 %s）' % bad[:4])

    # quest-spawn 的必须确实「世界表里没有这个人」
    fake = [(mid, nm) for (mid, nid, sh, x, y, nm, src) in qd.NPC_SPAWNS
            if str(src).startswith('quest-spawn')
            and any(v[0] == mid for v in pos.get(nm) or ())]
    check(not fake, '标 quest-spawn 的不与同图世界 NPC 重名（重 %s）' % fake[:4])

    # 场景别名：资料「清风村」→ mapid 1025；「珊瑚迷宫二层」→ 1036
    check(('1054', '金大有') and NPCID.get((1054, '金大有')) == 4333,
          '金大有按世界表落在明月镇 4333')
    check(NPCID.get((1025, '海外商人')) is not None and
          m.MAPS[1025]['name'] in (u'清风村2', u'清风村'),
          '资料「清风村」→ 本服 %s(1025)' % m.MAPS[1025]['name'])
    check(NPCID.get((1036, '江洋大盗')) is not None,
          '资料「珊瑚迷宫二层」→ 本服珊瑚迷宫(1036) 有落点')

    # 摆位全部落在本服已有的地图上
    bad_map = sorted({s[0] for s in qd.NPC_SPAWNS} - set(m.MAPS))
    check(not bad_map, '摆位地图全部存在（缺 %s）' % bad_map)

    # 同图 npcid 不重复（否则 _npc_name 会取错名字）
    dup = []
    for mid in {s[0] for s in qd.NPC_SPAWNS}:
        ids = [s[1] for s in qd.NPC_SPAWNS if s[0] == mid]
        if len(ids) != len(set(ids)):
            dup.append(mid)
    check(not dup, '同图 npcid 唯一（重复 %s）' % dup)

    # 任务态 NPC 只会在当前步骤出现，故「同图同坐标」不算冲突 —— 断言这一点成立
    overlap = {}
    for (mid, nid, sh, x, y, nm, src) in qd.NPC_SPAWNS:
        overlap.setdefault((mid, x, y), []).append((nm, src))
    both_world = [(k, v) for k, v in overlap.items()
                  if len(v) > 1
                  and all(not str(s).startswith('quest-spawn') for _n, s in v)]
    check(not both_world, '世界 NPC 之间无同坐标重叠（重 %s）' % both_world)
    print('     信息：资料表中同坐标的剧情 NPC %d 组（任务态互斥，不影响）'
          % len([1 for v in overlap.values() if len(v) > 1]))


# ── ③ 接取门 ───────────────────────────────────────────────────────────────
def test_gates(tmp):
    print('── ③ 接取门：等级 / 前置 ──')
    check(m.GRADE_MAX >= 80, '等级上限 ≥ 最高真值门槛 80（%d）' % m.GRADE_MAX)

    srv = make_server(tmp, 'g34', mapid=1054, grade=34)
    conn = FakeConn()
    look(srv, conn, NPCID[(1054, '金大有')])
    check(srv._quest_prompt is None and 107 not in srv.tasks,
          'Lv34 点金大有：接不了 35 级的「并雄的四国」')

    srv2 = make_server(tmp, 'g35', mapid=1054, grade=35)
    conn2 = FakeConn()
    look(srv2, conn2, NPCID[(1054, '金大有')])
    check(srv2._quest_prompt == {'qid': 107, 'action': 'accept'},
          'Lv35 点金大有：可接「并雄的四国」')
    answer(srv2, conn2, 1)
    check(srv2.tasks.get(107, {}).get('state') == m.TASK_STATE_DOING,
          '接取后 107 进入进行中')

    # 同一个 NPC 挂两条主线（金大有 = 7 和 12）：107 做完才轮到 112
    srv2.tasks.pop(107)
    srv2.quests_done.add(107)
    srv2.grade = 60                             # 112 的门槛是 60 级
    conn2.sent = []
    look(srv2, conn2, NPCID[(1054, '金大有')])
    check(srv2._quest_prompt == {'qid': 112, 'action': 'accept'},
          '107 完成后同一个金大有发 112（不会哑掉）')

    # 115 的前置是 110–114
    srv3 = make_server(tmp, 'p15', mapid=1019, grade=75)
    conn3 = FakeConn()
    look(srv3, conn3, NPCID[(1019, '龙蛇老人')])
    check(srv3._quest_prompt == {'qid': 110, 'action': 'accept'},
          'Lv75 点龙蛇老人：先发 110（无前置）')
    srv3.tasks.clear()
    srv3.quests_done.update((110, 111))
    conn3.sent = []
    look(srv3, conn3, NPCID[(1019, '龙蛇老人')])
    check(115 not in srv3.tasks, '前置只完成 110/111 时不给 115')
    check(has_op(conn3, m.OP_NPC_CHAT_EXT) if hasattr(m, 'OP_NPC_CHAT_EXT')
          else True, '前置不足 → 回一句解释（不弹接取）')
    srv3.quests_done.update((112, 113, 114))
    conn3.sent = []
    look(srv3, conn3, NPCID[(1019, '龙蛇老人')])
    check(srv3._quest_prompt == {'qid': 115, 'action': 'accept'},
          '前置补全 → 可接 115')


# ── ④ 全链路 107 ───────────────────────────────────────────────────────────
def test_chain_107(tmp):
    print('── ④ 并雄的四国 全链路（16 步）──')
    srv = make_server(tmp, 'c107', mapid=1054, grade=35)
    conn = FakeConn()
    q = qd.quest(107)
    step0 = q['steps'][0]['desc']

    look(srv, conn, NPCID[(1054, '金大有')])
    check(srv._quest_prompt == {'qid': 107, 'action': 'accept'}, '金大有可接取')
    conn.sent = []
    answer(srv, conn, 1)
    check(srv.tasks[107]['step'] == 0, '接取 → step 0')
    check(has_op(conn, m.OP_TASK_ADD), '下发 S2CTaskAdd')

    pt0 = srv.story_point
    for i, st in enumerate(q['steps']):
        do_step(srv, conn, 107, st)
        if st['kind'] == 'finish':
            continue
        want = i + 1
        if srv.tasks.get(107, {}).get('step') != want:
            check(False, '第 %d 步（%s）未推进到 %d（实际 %s）'
                  % (i, st['npc'], want, srv.tasks.get(107, {}).get('step')))
            return

    check(107 not in srv.tasks, '走完 16 步 → 任务 107 已结束')
    check(107 in srv.quests_done, 'quests_done 记 107')
    check(srv.story_point - pt0 == 3,
          '剧情点 +%d（一览表 3）' % (srv.story_point - pt0))
    # 交付物：丝绸手绢被扣掉，奖励的宝石包进背包
    left = sum(v['qty'] for v in srv.items.values() if v['shape'] == 25212)
    check(left == 0, '交付的丝绸手绢已扣除（剩 %d）' % left)
    got = sum(v['qty'] for v in srv.items.values() if v['shape'] == 72063)
    check(got == 2, '两处「1级宝石包」奖励到账 ×%d' % got)
    check(bool(srv.magics), '收尾步领悟招式（已学 %d 个）' % len(srv.magics))


# ── ⑤ 降级步 ───────────────────────────────────────────────────────────────
def test_downgraded():
    print('── ⑤ 本版查无的交付物 → 降级为对话（不造假 id、不卡流程）──')
    cases = [(107, u'刃虎宝宝'), (116, u'大螃蟹宝宝')]
    for qid, item in cases:
        hits = [st for st in qd.quest(qid)['steps']
                if u'note' in st and item in st['note']]
        check(len(hits) == 1, '%d 有 1 处「%s」降级步（%d）' % (qid, item, len(hits)))
        if hits:
            check(hits[0]['kind'] == 'talk',
                  '%d 的「%s」步已降级为 talk（%s）' % (qid, item, hits[0]['kind']))
            check(item not in qd.ITEM,
                  '「%s」确实不在本版 ITEM 表（没编假 id）' % item)
    # 反证：降级步若仍是 item，会因为 item 不在表里而永远过不去
    # （any_shapes 的多 shape 分支不算：它走 _quest_item_targets 的另一条路）
    stuck = [(qid, i) for qid, q in qd.QUESTS.items()
             for i, st in enumerate(q['steps'])
             if st['kind'] == 'item' and not st.get('any_shapes') and
             qd.ITEM.get(st['item'], (None,))[0] is None]
    check(not stuck, '没有 shape 为空却仍走 item 分支的步（%s）' % stuck)


# ── ⑥ 任务态可见性 ─────────────────────────────────────────────────────────
def test_visibility(tmp):
    print('── ⑥ 任务态 NPC 可见性（同图两个剧情 NPC 只显示当前步骤的）──')
    srv = make_server(tmp, 'vis', mapid=1020, grade=35)
    conn = FakeConn()
    srv.tasks[107] = {'state': m.TASK_STATE_DOING, 'progress': 0,
                      'step': 0, 'qid': 107}
    goto(srv, 1020)
    vis0 = set(srv._visible_npc_ids())
    check(8143 not in vis0 and 8144 not in vis0,
          'step0（目标在海底神殿）琉璃港不显示孙秀才/锦毛鼠')
    check(4242 not in vis0, '海底神殿的加洛国王不在琉璃港出现')

    srv.tasks[107]['step'] = 1
    vis1 = set(srv._visible_npc_ids())
    check(8143 in vis1 and 8144 not in vis1, 'step1 只显示孙秀才')
    srv.tasks[107]['step'] = 2
    vis2 = set(srv._visible_npc_ids())
    check(8144 in vis2 and 8143 not in vis2, 'step2 只显示锦毛鼠')

    # 世界 NPC 常显：回到海底神殿，加洛国王与龙蛇老人都在
    goto(srv, 1019)
    srv.tasks.clear()
    vis3 = set(srv._visible_npc_ids())
    check(4242 in vis3 and 4336 in vis3,
          '世界 NPC（加洛国王 4242 / 龙蛇老人 4336）不依赖任务，常显')

    # 场景下发与可见集同源
    goto(srv, 1020)
    srv.tasks[107] = {'state': m.TASK_STATE_DOING, 'progress': 0,
                      'step': 1, 'qid': 107}
    conn.sent = []
    srv._send_scene(conn, 100, 100)
    sent_ids = set()
    for f in conn.sent:
        n = f[0]
        body = f[1:]
        if n == 0xFF:
            n = struct.unpack('<H', f[1:3])[0]
            body = f[3:]
        if body and body[0] == m.OP_MAP_ADD_NPC:
            sent_ids.add(struct.unpack('<I', body[1:5])[0])
    check(sent_ids == set(srv._visible_npc_ids()),
          'S2CMapAddNpc 下发的 NPC 集 == 可见集（%d 个）' % len(sent_ids))


# ── ⑦ 17–22 不可得 ────────────────────────────────────────────────────────
def test_blocked():
    print('── ⑦ 17–22 不可得（登记齐全、缺口可查）──')
    ni = qd.NOT_IMPLEMENTED
    check(len(ni) == 6, '登记 6 条（实际 %d）' % len(ni))
    check(not (set(ni) & set(qd.QUESTS)), '未落地条目不在 QUESTS 里（不假装已做）')
    bad = [no for no, v in ni.items()
           if not v.get('blocked') or not v.get('missing_npcs')
           or not v.get('name') or not v.get('npc') or not v.get('map')]
    check(not bad, '每条都有 name/npc/map/blocked/missing_npcs（缺 %s）' % bad)
    spawnable = {s[5] for s in qd.NPC_SPAWNS}
    fake = {no: [n for n in v['missing_npcs'] if n in spawnable]
            for no, v in ni.items()}
    fake = {k: v for k, v in fake.items() if v}
    check(not fake,
          '缺的名字确实一个都摆不出来（能摆 %s）—— 反证不造坐标' % fake)
    print('     17–22 缺口：%s'
          % '；'.join('%d 缺 %d 个 NPC' % (no, len(v['missing_npcs']))
                     for no, v in sorted(ni.items())))


# ── ⑧ 落盘往返 ─────────────────────────────────────────────────────────────
def test_persist(tmp):
    print('── ⑧ 落盘往返：完成任务后存档 → 新实例读档 ──')
    shared = os.path.join(tmp, 'p716')
    srv = make_server(tmp, 'p1', mapid=1054, grade=35, savedir=shared)
    conn = FakeConn()
    q = qd.quest(107)
    accept(srv, conn, 107)
    for st in q['steps']:
        do_step(srv, conn, 107, st)
    check(107 in srv.quests_done, '完成后 107 在 quests_done')
    pt = srv.story_point
    check(srv.flush_saves(timeout=5.0), 'flush_saves 成功')

    srv2 = make_server(tmp, 'p2', mapid=1054, grade=35, savedir=shared)
    check(107 in getattr(srv2, 'quests_done', set()),
          '读档后 quests_done 含 107')
    check(int(getattr(srv2, 'story_point', 0)) == pt,
          '读档后剧情点 %d == %d' % (int(getattr(srv2, 'story_point', 0)), pt))


def main():
    tmp = tempfile.mkdtemp(prefix='q716_')
    try:
        test_data()
        test_placement()
        test_gates(tmp)
        test_chain_107(tmp)
        test_downgraded()
        test_visibility(tmp)
        test_blocked()
        test_persist(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print('== %s ==' % ('全部通过' if ok else '有失败'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
