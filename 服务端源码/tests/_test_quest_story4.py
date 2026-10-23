# -*- coding: utf-8 -*-
"""主线剧情 4–6 与「任务生命周期」——回归测试（进程内，不走 socket）。

覆盖：
  ① 数据自洽：1–6 的编号/步骤数/等级/前置/剧情点合计，与《资料全库》一览表逐条对齐；
     每个步骤 NPC 都有摆位、剧情物品 shape 都能在客户端 itemdata 里查到、
     每处战斗都标了 shape_sim。
  ② 接取门：等级不足 / 前置未完成（用一条带 prev 的临时剧情验证引擎）/ 已有主线进行中。
  ③ 剧情 4「哭泣的森林」全链路（14 步：3 场战斗 + 11 步对话，剧情点 1+1+1）。
  ④ 剧情 5「文武的考验」全链路（14 步：含 2 处交物品，数量不足必须被拒）。
  ⑤ 剧情 6「勇者的宝玉」全链路（12 步：交物品步也发剧情点，`_quest_grant` 不重不漏）。
  ⑥ 失败重试：剧情战斗战败 → 步骤不前进、`_quest_battle` 作废、可原地重打。
  ⑦ 放弃：进行到一半放弃 → 任务清空、步骤归零、剧情点不回退。
  ⑧ 重登恢复：存盘 → 新实例读档 → 进世界时**主动推** S2CTaskAdd 且步骤续上。
  ⑨ 剧情点用途：本版客户端无消费入口 → FEATURE_STATUS 必须是 display_only。

用法：python _test_quest_story4.py
"""
from __future__ import print_function

import io
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')            # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):                 # 兼容「tests 与 sim 平级」的旧布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)


def _mx_root(start):
    """向上找含「客户端源码」的工程根（本工作区 tests 与 sim 都嵌在 服务端源码 里）。"""
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
import juqing_skill_data as jqmod   # noqa: E402

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

    def ops(self):
        out = []
        for f in self.sent:
            n = f[0]
            body = f[1:]
            if n == 0xFF:
                n = struct.unpack('<H', f[1:3])[0]
                body = f[3:]
            out.append((body[0], body[1:]))
        return out


_seq = [0]


def make_server(tmp, tag, mapid=1054, grade=30, savedir=None):
    _seq[0] += 1
    sub = savedir or os.path.join(tmp, 'case%d_%s' % (_seq[0], tag))
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'), savedir=os.path.join(sub, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv.mapid = mapid
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    srv.grade = grade
    return srv


NPCID = {(mid, name): nid for (mid, nid, _sh, _x, _y, name, _src)
         in qd.NPC_SPAWNS}


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
    """按步骤类型走一步（对话/交物品/战斗/收尾），返回 None。"""
    goto(srv, st['map'])
    if st['kind'] == 'item':
        shape, name = qd.ITEM[st['item']]
        srv._grant_item(conn, shape, name, int(st.get('qty', 1)))
    look(srv, conn, NPCID[(st['map'], st['npc'])])
    answer(srv, conn, 1)


# ── ① 数据自洽 ─────────────────────────────────────────────────────────────
def test_data():
    print('── ① 数据自洽（1–16 与一览表对齐）──')
    # ★ 2026-10-22：7–16 已落地（qid 107–116），本表随之扩到 16 条；
    #   7–16 的逐条细项（坐标双源/降级步/前置/可见性）另有 _test_quest_716 专测。
    expect = {101: (1, 7, 10, 0), 102: (2, 9, 20, 2), 103: (3, 11, 30, 3),
              104: (4, 14, 30, 3), 105: (5, 14, 30, 3), 106: (6, 12, 30, 3),
              107: (7, 16, 35, 3), 108: (8, 13, 40, 3), 109: (9, 16, 45, 3),
              110: (10, 13, 50, 3), 111: (11, 11, 55, 3), 112: (12, 15, 60, 3),
              113: (13, 16, 65, 3), 114: (14, 16, 70, 3), 115: (15, 5, 75, 1),
              116: (16, 13, 80, 2)}
    got = {qid: (q['no'], len(q['steps']), q['level'], q['point'])
           for qid, q in qd.QUESTS.items()}
    check(got == expect, '编号/步数/等级/剧情点 与一览表一致（%s）' % got)

    # 每条剧情的剧情点 = 各步 point 之和（含 finish 步）——防止「表里写 3、实发 2」
    bad = []
    for qid, q in qd.QUESTS.items():
        total = sum(int((st.get('reward') or {}).get('point') or 0) for st in q['steps'])
        if total != q['point']:
            bad.append((qid, total, q['point']))
    check(not bad, '剧情点合计 = 一览表（不等 %s）' % bad)

    # 步骤 NPC 必须在摆位表里
    bad_step = []
    for qid, q in qd.QUESTS.items():
        if (q['start_map'], q['start_npc']) not in NPCID:
            bad_step.append(('start', qid, q['start_npc']))
        for i, st in enumerate(q['steps']):
            if (st['map'], st['npc']) not in NPCID:
                bad_step.append((qid, i, st['npc']))
            if st['map'] not in m.MAPS:
                bad_step.append((qid, i, 'map', st['map']))
    check(not bad_step, '每步 NPC 都有摆位、地图都存在（缺 %s）' % bad_step)

    # 剧情物品
    src = io.open(os.path.join(CLIENT_SRC, 'data', 'itemdata.py'),
                  encoding='utf-8', errors='ignore').read()
    import ast
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

    # 每处战斗都必须标 shape_sim（造型是模拟，不能装真值）
    fights = [(qid, i) for qid, q in qd.QUESTS.items()
              for i, st in enumerate(q['steps']) if st['kind'] == 'fight']
    unmarked = [(qid, i) for (qid, i) in fights
                if not qd.QUESTS[qid]['steps'][i].get('shape_sim')]
    check(len(fights) == 49 and not unmarked,
          '战斗步骤 %d 处（1–16 合计），全部标 shape_sim（漏标 %s）'
          % (len(fights), unmarked))
    # 战斗步骤必须有坐标 + 造型（客户端要按坐标找 NPC）
    bad_fight = [(qid, i) for (qid, i) in fights
                 if 'x' not in qd.QUESTS[qid]['steps'][i]
                 or 'shape' not in qd.QUESTS[qid]['steps'][i]]
    check(not bad_fight, '战斗步骤都带坐标与造型（缺 %s）' % bad_fight)

    # 前置：资料里 1–6 无跨任务前置；15/17/18/22 才有，已记在 LATER_PREV。
    # ★ 2026-10-22：15 已落地（qid 115），前置从「登记项」变成真值
    #   `QUESTS[115]['prev']`；LATER_PREV 里保留 15 只是留档，不再参与引擎。
    check(all(not qd.prev_of(q) for q in (101, 102, 103, 104, 105, 106)),
          '1–6 无跨任务前置（与一览表一致）')
    check(qd.LATER_PREV.get(15) == (10, 11, 12, 13, 14),
          '资料明确写死的剧情 15 前置已登记')
    check(qd.QUESTS[115].get('prev') == (110, 111, 112, 113, 114),
          '剧情 15 的前置已落到引擎真值 prev（按 qid 不是编号）')
    # 7–16 全部落地后，缺口只剩 17–22 这 6 条
    check(len(qd.NOT_IMPLEMENTED) == 6
          and sorted(qd.NOT_IMPLEMENTED) == [17, 18, 19, 20, 21, 22],
          '17–22 未实现条目登记 %d 条（可查、不假装已做）'
          % len(qd.NOT_IMPLEMENTED))


# ── ② 接取门 ───────────────────────────────────────────────────────────────
def test_gates(tmp):
    print('── ② 接取门：等级 / 前置 / 单主线 ──')
    # (a) 等级不足
    srv = make_server(tmp, 'g1', mapid=1007, grade=29)
    conn = FakeConn()
    look(srv, conn, NPCID[(1007, '护林长老')])
    check(srv._quest_prompt is None and 104 not in srv.tasks,
          'Lv29 点护林长老：接不了 30 级的「哭泣的森林」')

    # (b) 前置未完成：临时插一条 prev=(104,) 的假剧情，验证引擎真的会挡。
    #     ★ 必须挂在**没有**其他剧情的 NPC 上（这里用明月镇的镇长）：护林长老
    #       本身是 104 的领取人，候选里混着一条没 prev 的，测的就不是 prev 了。
    fake = {'qid': 990, 'no': 99, 'name': '前置校验用', 'level': 1, 'country': None,
            'point': 0, 'prev': (104,), 'start_map': 1025, 'start_npc': '老农夫',
            'intro': 'P\n测试。\nQ好。', 'accept': 'P\n测试。\nQ好。',
            'steps': [{'kind': 'finish', 'map': 1025, 'npc': '老农夫',
                       'desc': '测试', 'text': 'P\n测试。\nQ好。'}]}
    qd.QUESTS[990] = fake
    try:
        srv2 = make_server(tmp, 'g2', mapid=1025, grade=30)
        conn2 = FakeConn()
        look(srv2, conn2, NPCID[(1025, '老农夫')])
        check(srv2._quest_prompt is None,
              '有 prev 且未完成 → 不弹接取确认（引擎按 prev 校验）')
        check(990 not in srv2.tasks, '前置未完成 → 没有偷偷接上')
        srv2.quests_done.add(104)
        look(srv2, conn2, NPCID[(1025, '老农夫')])
        check(srv2._quest_prompt and srv2._quest_prompt['qid'] == 990,
              'prev 完成后 → 可接')
    finally:
        qd.QUESTS.pop(990, None)

    # (c) 单主线约束
    srv3 = make_server(tmp, 'g3', mapid=1001, grade=30)
    conn3 = FakeConn()
    accept(srv3, conn3, 105)
    check(srv3.tasks.get(105, {}).get('step') == 0, 'Lv30 接「文武的考验」')
    goto(srv3, 1007)
    look(srv3, conn3, NPCID[(1007, '护林长老')])
    check(srv3._quest_prompt is None and 104 not in srv3.tasks,
          '已有主线进行中 → 拒绝接第二条（引擎约束）')


# ── ③④⑤ 三条主线的全链路 ──────────────────────────────────────────────────
def run_chain(tmp, qid, tag):
    q = qd.quest(qid)
    srv = make_server(tmp, tag, mapid=q['start_map'], grade=q['level'])
    conn = FakeConn()
    accept(srv, conn, qid)
    check(srv.tasks.get(qid, {}).get('step') == 0,
          '接「%s」→ step 0（%d 步）' % (q['name'], len(q['steps'])))
    sp0 = srv.story_point
    for i, st in enumerate(q['steps']):
        assert srv.tasks.get(qid, {}).get('step') == i, (qid, i, srv.tasks.get(qid))
        do_step(srv, conn, qid, st)
        if st['kind'] == 'fight':
            assert srv.in_war, (qid, i, '未开战')
            assert srv.enemy_meta[1]['name'] == st['npc'], (qid, i, srv.enemy_meta)
            assert srv.enemy_meta[1]['shape'] == st['shape'], (qid, i, srv.enemy_meta)
            srv._after_hero_action(conn, dead_ids=[1])
            assert not srv.in_war, (qid, i, '战斗未结束')
    check(qid in srv.quests_done, '「%s」完成并记入 quests_done' % q['name'])
    check(qid not in srv.tasks, '完成后从任务栏移除')
    check(srv.story_point - sp0 == q['point'],
          '本条线剧情点 +%d（实 +%d）' % (q['point'], srv.story_point - sp0))
    return srv


def test_quest4(tmp):
    print('── ③ 哭泣的森林 全链路 ──')
    srv = run_chain(tmp, 104, 'q4')
    check(any(v['shape'] == 25404 for v in srv.items.values()), '奖励宠物作战心得')
    check(any(v['shape'] == 72063 for v in srv.items.values()), '奖励一级如意宝石包（1级宝石包）')
    _yao = [v for v in srv.items.values() if v['shape'] == 25405]
    check(sum(int(v['qty']) for v in _yao) == 2, '复命奖励魔兽要诀 ×2')


def test_quest5(tmp):
    print('── ④ 文武的考验：交物品与数量校验 ──')
    srv = make_server(tmp, 'q5', mapid=1001, grade=30)
    conn = FakeConn()
    accept(srv, conn, 105)
    # 快进到 step1（慧持要 2 棵伤寒草）
    srv.tasks[105]['step'] = 1
    goto(srv, 1022)
    look(srv, conn, NPCID[(1022, '慧持')])
    check(srv._quest_prompt is None and srv.tasks[105]['step'] == 1,
          '空手点慧持：不弹确认、不推进')
    srv._grant_item(conn, 21221, '伤寒草', 1)
    look(srv, conn, NPCID[(1022, '慧持')])
    check(srv._quest_prompt is None, '只有 1 棵（需 2）仍不放行')
    srv._grant_item(conn, 21221, '伤寒草', 1)
    look(srv, conn, NPCID[(1022, '慧持')])
    check(srv._quest_prompt == {'qid': 105, 'action': 'item'}, '凑够 2 棵 → 可交付')
    answer(srv, conn, 1)
    check(srv.tasks[105]['step'] == 2, '交伤寒草 → step 2')
    check(not [v for v in srv.items.values() if v['shape'] == 21221], '伤寒草已扣除')
    # 全链路
    run_chain(tmp, 105, 'q5b')


def test_quest6(tmp):
    print('── ⑤ 勇者的宝玉：交物品步也发剧情点 ──')
    srv = make_server(tmp, 'q6', mapid=1013, grade=30)
    conn = FakeConn()
    accept(srv, conn, 106)
    srv.tasks[106]['step'] = 2                  # 给美人鱼草编花环（该步 reward.point=1）
    sp0 = srv.story_point
    goto(srv, 1023)
    look(srv, conn, NPCID[(1023, '美人鱼')])
    check(srv._quest_prompt is None, '空手点美人鱼不放行')
    srv._grant_item(conn, 25203, '草编花环', 1)
    look(srv, conn, NPCID[(1023, '美人鱼')])
    answer(srv, conn, 1)
    check(srv.tasks[106]['step'] == 3, '交花环 → step 3')
    check(srv.story_point == sp0 + 1,
          '交物品步的剧情点 +1 真的发了（%d→%d）' % (sp0, srv.story_point))
    check(not [v for v in srv.items.values() if v['shape'] == 25203], '草编花环已扣除')
    run_chain(tmp, 106, 'q6b')


# ── ⑥ 失败重试 ─────────────────────────────────────────────────────────────
def test_retry(tmp):
    print('── ⑥ 剧情战斗战败 → 可原地重打 ──')
    srv = make_server(tmp, 'retry', mapid=1007, grade=30)
    conn = FakeConn()
    accept(srv, conn, 104)
    srv.tasks[104]['step'] = 4                  # 幽鬼火（绿湾林地 186,110）
    goto(srv, 1011)
    look(srv, conn, NPCID[(1011, '幽鬼火')])
    answer(srv, conn, 1)
    check(srv.in_war and srv._quest_battle, '开打：_quest_battle 已记录')
    # 模拟主角倒下：敌方连续命中直到 HP 归零
    srv._pet_alive = False                 # 只有主角在场上，_enemy_attack 必打主角
    srv.hero_hp = 1
    srv._enemy_attack(conn, 1)
    check(not srv.in_war, '主角倒地 → 战斗结束')
    check(srv._quest_battle is None, '战败即作废 _quest_battle（不会顶着旧上下文）')
    check(srv.tasks[104]['step'] == 4, '战败不推进步骤（仍停在第 5 步）')
    # 再打一次（这次赢）
    look(srv, conn, NPCID[(1011, '幽鬼火')])
    check(srv._quest_prompt == {'qid': 104, 'action': 'fight'}, '可以原地重打')
    answer(srv, conn, 1)
    srv._after_hero_action(conn, dead_ids=[1])
    check(srv.tasks[104]['step'] == 5, '重打获胜 → step 5')
    check(srv.story_point == 1, '战败那次的剧情点没重复发（=1）')


# ── ⑦ 放弃 ─────────────────────────────────────────────────────────────────
def test_giveup(tmp):
    print('── ⑦ 放弃主线：清任务、归零、剧情点不回退 ──')
    srv = make_server(tmp, 'give', mapid=1001, grade=30)
    conn = FakeConn()
    accept(srv, conn, 105)
    srv.tasks[105]['step'] = 4                  # 已经打过无赖，剧情点 +1
    srv.story_point = 1
    srv._persist()
    conn.sent = []
    srv._dispatch(conn, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, 105))
    check(105 not in srv.tasks, '放弃后任务从 tasks 移除')
    check(any(op == m.OP_TASK_REMOVE for op, _b in conn.ops()),
          '下发 S2CTaskRemove(73)')
    check(srv.story_point == 1, '已赚到的剧情点不回退')
    check(105 not in srv.quests_done, '放弃不记入 quests_done（能重接）')
    # 重新接：步骤归零
    goto(srv, 1001)
    look(srv, conn, NPCID[(1001, '汪太公')])
    check(srv._quest_prompt and srv._quest_prompt['qid'] == 105, '放弃后可重新接取')
    answer(srv, conn, 1)
    check(srv.tasks[105]['step'] == 0, '重接后步骤归零（不是接着第 5 步）')


# ── ⑧ 重登恢复 ─────────────────────────────────────────────────────────────
def test_relogin(tmp):
    print('── ⑧ 重登恢复：推 TaskAdd + 步骤续上 ──')
    shared = os.path.join(tmp, 'relogin')
    srv = make_server(tmp, 'relogin1', mapid=1007, grade=30, savedir=shared)
    conn = FakeConn()
    accept(srv, conn, 104)
    srv.tasks[104]['step'] = 3                  # 走到「去绿湾林地找老森」
    srv.story_point = 1
    srv._persist()

    srv2 = make_server(tmp, 'relogin2', mapid=1007, grade=30, savedir=shared)
    check(srv2.tasks.get(104, {}).get('step') == 3,
          '新实例读档：任务 104 停在第 3 步（读回 %s）'
          % (srv2.tasks.get(104) or {}).get('step'))
    c2 = FakeConn()
    srv2._enter_world(c2)
    adds = [b for op, b in c2.ops() if op == m.OP_TASK_ADD]
    check(len(adds) == 1, '进世界主动推 S2CTaskAdd（%d 条）' % len(adds))
    if adds:
        tid = struct.unpack('<H', adds[0][:2])[0]
        check(tid == 104, '推的是主线 104（tid=%d）' % tid)
        desc = adds[0][4:].split(b'\x00')[0].decode('gbk', 'replace')
        want = qd.step_of(104, 3)['desc']
        check(want.encode('gbk') in adds[0], '描述 = 当前步骤目标「%s」' % want)
        check('老森' in desc or want.encode('gbk') in adds[0], '（描述内容：%s）' % desc)
    # 继续往下走：老森这一步能推进
    goto(srv2, 1011)
    look(srv2, FakeConn(), NPCID[(1011, '老森')])
    check(srv2._quest_prompt == {'qid': 104, 'action': 'talk'},
          '重登后当前步骤 NPC 仍能推进')


# ── ⑨ 剧情点：真实用途 = 学剧情技能 ───────────────────────────────────────
def test_story_point(tmp):
    print('── ⑨ 剧情点花得出去：学/升剧情技能 ──')
    check(m.FEATURE_STATUS.get('story_point') == 'implemented',
          'FEATURE_STATUS 标 implemented（2026-10-22 核实：有消费入口）')
    check(len(jqmod.JUQING_SKILLS) >= 20,
          '剧情技能表 %d 条（客户端 skilldata 2001-2029）' % len(jqmod.JUQING_SKILLS))
    check(all(2 <= v['dist'] <= 4 and 3 <= v['max'] <= 8 for v in jqmod.JUQING_SKILLS.values()),
          '每级 2–4 点、最高 3–8 级（juqingdata 真值）')

    # 客户端证据：dlglearnskill 对 2001–2029 用 m_StoryPoint 当花费列
    dlg = io.open(os.path.join(CLIENT_SRC, 'dialog', 'dlglearnskill.py'),
                  encoding='utf-8', errors='ignore').read()
    check("m_StoryPoint" in dlg and '2001 <= item.m_SpecialSkill[0]' in dlg,
          '客户端学习面板对 2001–2029 用 m_StoryPoint 判花费（真值）')

    # 学 1 级：扣点、发 sktype=3 回执、状态进存档
    shared = os.path.join(tmp, 'sp')
    srv = make_server(tmp, 'sp', mapid=1054, grade=30, savedir=shared)
    srv.story_point = 5
    conn = FakeConn()
    skid = 2001                                   # 仙灵店铺：max=5 dist=3
    check(jqmod.JUQING_SKILLS[skid]['dist'] == 3, '仙灵店铺每级 3 点（真值）')
    ok = srv._hero_learn_magic(conn, skid)
    check(ok and srv.juqing_skills.get(skid) == 1, '学剧情技能 → 1 级')
    check(srv.story_point == 2, '剧情点 5-3=2（实 %d）' % srv.story_point)
    lv = [b for op, b in conn.ops() if op == m.OP_SKILL_LEVEL]
    check(lv and lv[0][0] == 3 and struct.unpack('<H', lv[0][1:3])[0] == skid
          and lv[0][3] == 1, '回执 S2CSkillLevel sktype=3（%s）' % (lv[0].hex() if lv else None))
    check(any(op == m.OP_PROP_CHANGE and b and b[0] == m.PROP_IDX_STORY_POINT
              for op, b in conn.ops()),
          '同步剧情点资源条（PropChange 42）')

    # 再升 1 级就点不够 → 整包拒绝、点数不变
    conn2 = FakeConn()
    ok2 = srv._hero_learn_magic(conn2, skid)
    check(not ok2 and srv.juqing_skills[skid] == 1 and srv.story_point == 2,
          '剧情点不足 → 拒绝且点数不动（2 < 3）')

    # 存盘/读档：等级与剩余点数都在
    srv._persist()
    srv2 = make_server(tmp, 'sp2', mapid=1054, grade=30, savedir=shared)
    check(srv2.juqing_skills.get(skid) == 1 and srv2.story_point == 2,
          '读档：剧情技能 1 级、剩 2 点（%s / %d）'
          % (srv2.juqing_skills.get(skid), srv2.story_point))

    # 满级：把点补足连学到 max，然后拒绝
    srv3 = make_server(tmp, 'sp3', mapid=1054, grade=30)
    srv3.story_point = 999
    _max = jqmod.JUQING_SKILLS[skid]['max']
    for _i in range(_max):
        srv3._hero_learn_magic(FakeConn(), skid)
    check(srv3.juqing_skills[skid] == _max, '连学到上限 %d 级' % _max)
    before = srv3.story_point
    check(not srv3._hero_learn_magic(FakeConn(), skid)
          and srv3.story_point == before, '满级后不再扣点')

    # 师傅课表里必须带上剧情技能（否则玩家无从发起）
    srv4 = make_server(tmp, 'sp4', mapid=1054, grade=30)
    c4 = FakeConn()
    srv4._send_learn_list(c4)
    lst = [b for op, b in c4.ops() if op == m.OP_NPC_LEARN_LIST]
    check(lst and struct.pack('<H', skid) in lst[0],
          '学习列表含剧情技能 %d（客户端才会列出这一行）' % skid)

    # GM 控制台：point / juqing 与上面走同一条真实链（不是旁路置数）
    srv5 = make_server(tmp, 'sp5', mapid=1054, grade=30)
    r = srv5.gm_command('point 6')
    check(r.get('ok') and srv5.story_point == 6, 'GM point 6 → 剧情点 6')
    r = srv5.gm_command('juqing %d' % skid)
    check(r.get('ok') and srv5.juqing_skills.get(skid) == 1 and srv5.story_point == 3,
          'GM juqing → 学 1 级、扣 3 点（%s）' % r.get('msg'))
    check('juqing' in srv5._GM_CMD_HELP and 'point' in srv5._GM_CMD_HELP,
          'help 里列了这两条（不写出来的命令等于没有）')


def main():
    tmp = tempfile.mkdtemp(prefix='m4_quest456_')
    try:
        test_data()
        test_gates(tmp)
        test_quest4(tmp)
        test_quest5(tmp)
        test_quest6(tmp)
        test_retry(tmp)
        test_giveup(tmp)
        test_relogin(tmp)
        test_story_point(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
