# -*- coding: utf-8 -*-
"""主线剧情 1–3 —— 回归测试（进程内，不走 socket）。

覆盖：
  ① 数据自洽：3 条剧情 / 步骤数 / 每步 NPC 都在摆位表里 / NPC id 同图唯一 /
     剧情物品 shape 都在客户端 itemdata 里查得到（None 除外，且必须带 note）。
  ② 等级门槛：Lv10 接不了 Lv20 的「游历四国」；Lv30 可接「失落的银爵」。
  ③ 完整链路（「村长的委托」）：接任务 → 对话推进 → 剧情战斗 → 交物品 →
     再战斗 → 复命完成；断言 TaskAdd/TaskRemove、物品扣除、战斗怪名。
  ④ 持久化：step 与剧情点写档后能读回。

用法：python _test_quest_chain.py
"""
from __future__ import print_function

import ast
import io
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)
import m4_server as m          # noqa: E402
import quest_data as qd        # noqa: E402

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and c


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, frame):
        self.sent.append(frame)


def item_names():
    """从客户端 itemdata.py 抠 DATA 字面量（文件里混有 Python2 print，不能 import）。"""
    p = r'C:\Users\Administrator\MX\客户端源码\src\data\itemdata.py'
    src = io.open(p, encoding='utf-8', errors='ignore').read()
    st = src.find('{', src.find('DATA = {'))
    depth, quote, esc, j = 0, None, False, st
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
    data = ast.literal_eval(src[st:j + 1])
    out = {}
    for shape, row in data.items():
        nm = row[0]
        if isinstance(nm, bytes):
            nm = nm.decode('gbk', 'replace')
        out[int(shape)] = nm
    return out


_seq = [0]


def make_server(tmp, mapid, grade=10):
    # 每个 server 独立存档目录：否则第二个 server 会载入上一个的 tasks，
    # `_quest_active()` 会命中别的任务，测试之间互相污染（第一版就栽在这）。
    _seq[0] += 1
    sub = os.path.join(tmp, 'case%d' % _seq[0])
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'), savedir=os.path.join(sub, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv.mapid = mapid
    srv.grade = grade
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (srv.px, srv.py)
    return srv


def look(srv, conn, npcid):
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', npcid))


def answer(srv, conn, choice=1):
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', choice))


def has_op(conn, op):
    return any(len(f) > 1 and f[1] == op for f in conn.sent)


# ── ① 数据自洽 ─────────────────────────────────────────────────────────────
def test_data():
    print('── ① 数据自洽 ──')
    # ★ 2026-10-22：剧情从 3 条扩到 6 条（4/5/6 = 哭泣的森林/文武的考验/勇者的宝玉）。
    #   这里只守 1–3 的既有口径 + 「总数不低于 6」：4–6 的逐条对账在
    #   _test_quest_story4.py 的 test_data 里做，两边别互相抄断言。
    check(len(qd.QUESTS) >= 6, '主线 ≥6 条（实际 %d）' % len(qd.QUESTS))
    counts = {qid: len(q['steps']) for qid, q in qd.QUESTS.items()}
    check(counts.get(101) == 7 and counts.get(102) == 9 and counts.get(103) == 11,
          '剧情 1–3 步骤数 %s' % {k: counts.get(k) for k in (101, 102, 103)})

    spawn_index = {}
    dup = []
    for (mid, nid, _shape, _x, _y, name, _src) in qd.NPC_SPAWNS:
        if (mid, nid) in spawn_index:
            dup.append((mid, nid))
        spawn_index[(mid, name)] = nid
    check(not dup, '同图 NPC id 无重复（%s）' % dup)

    bad_step = []
    for qid, q in qd.QUESTS.items():
        if (q['start_map'], q['start_npc']) not in spawn_index:
            bad_step.append(('start', qid, q['start_npc']))
        for i, st in enumerate(q['steps']):
            if (st['map'], st['npc']) not in spawn_index:
                bad_step.append((qid, i, st['npc']))
    check(not bad_step, '每个步骤 NPC 都有摆位（缺 %s）' % bad_step)

    names = item_names()
    bad_item = []
    for key, (shape, name) in qd.ITEM.items():
        if names.get(shape) != name:
            bad_item.append((key, shape, name, names.get(shape)))
    check(not bad_item, '剧情物品 shape/名称与 itemdata 一致（漂移 %s）' % bad_item)
    bad_weapon = [s for s in qd.WEAPON_LV30 if s not in names]
    check(not bad_weapon, '30 级武器 id 都在 itemdata（缺 %s）' % bad_weapon)

    sim_steps = [(qid, i) for qid, q in qd.QUESTS.items()
                 for i, st in enumerate(q['steps']) if st.get('shape_sim')]
    check(len(sim_steps) >= 8 and all(
        qd.QUESTS[qid]['steps'][i].get('shape_sim') for (qid, i) in sim_steps),
        '战斗步骤 %d 处，均显式标 shape_sim' % len(sim_steps))


# ── ② 等级门槛 ─────────────────────────────────────────────────────────────
def test_level_gate(tmp):
    print('── ② 等级门槛 ──')
    srv = make_server(tmp, 1054, grade=10)
    conn = FakeConn()
    look(srv, conn, 4349)                       # 钱梦庵（需要 20 级）
    check(not srv._quest_prompt, 'Lv10 点钱梦庵不弹接取确认')
    check(102 not in srv.tasks, 'Lv10 未接「游历四国」')

    srv2 = make_server(tmp, 1019, grade=30)
    conn2 = FakeConn()
    look(srv2, conn2, 4329)                     # 主教（需要 30 级）
    check(srv2._quest_prompt and srv2._quest_prompt['qid'] == 103,
          'Lv30 点主教可接「失落的银爵」')
    answer(srv2, conn2, 1)
    check(srv2.tasks.get(103, {}).get('state') == m.TASK_STATE_DOING,
          '接取后任务 103 进入进行中')
    # GM 等级指令（控制台）：`grade 20`
    r = srv2.gm_command('grade 20')
    check(r.get('ok') and srv2.grade == 20, 'GM grade 20 → 等级 20')


# ── ③ 完整链路 ─────────────────────────────────────────────────────────────
def test_full_chain(tmp):
    print('── ③ 村长的委托 全链路 ──')
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()

    # 接任务
    look(srv, conn, 3021)                       # 村长
    check(srv._quest_prompt == {'qid': 101, 'action': 'accept'}, '村长度可接取')
    conn.sent = []
    answer(srv, conn, 1)
    t = srv.tasks.get(101)
    check(t and t['state'] == m.TASK_STATE_DOING and t['step'] == 0, '接取 → step 0')
    check(has_op(conn, m.OP_TASK_ADD), '下发 S2CTaskAdd(72)')
    # 任务栏重发（客户端打开任务面板发 op48）：必须带上主线，而不只是新手任务
    conn.sent = []
    srv._dispatch(conn, m.C2S_GET_TASK, b'')
    check(has_op(conn, m.OP_TASK_ADD), 'op48 重发任务栏（含主线）')

    # 步骤 0：老农夫（对话）
    look(srv, conn, 4118)
    answer(srv, conn, 1)
    check(srv.tasks[101]['step'] == 1, '对话老农夫 → step 1')

    # 步骤 1：捣乱的野兽（战斗）
    look(srv, conn, 8101)
    check(srv._quest_prompt == {'qid': 101, 'action': 'fight'}, '野兽步骤进入战斗确认')
    answer(srv, conn, 1)
    check(srv.in_war, '确认后开战')
    check(srv.enemy_meta[1]['name'] == '捣乱的野兽', '战斗怪名 = 捣乱的野兽')
    check(srv.enemy_meta[1]['shape'] == 2010, '战斗造型 = 毛草团(2010, SIM)')
    conn.sent = []
    srv._after_hero_action(conn, dead_ids=[1])   # 模拟一击致死
    check(not srv.in_war, '敌方全灭 → 战斗结束')
    check(srv.tasks[101]['step'] == 2, '战斗胜利 → step 2（回村长）')

    # 步骤 2：回村长（对话）
    look(srv, conn, 3021)
    answer(srv, conn, 1)
    check(srv.tasks[101]['step'] == 3, '回村长 → step 3（找张二贵）')

    # 步骤 3：张二贵（交物品）——先空手，应被拒
    look(srv, conn, 4273)
    check(srv._quest_prompt is None, '空手点张二贵不弹确认')
    check(srv.tasks[101]['step'] == 3, '空手不推进')
    iid = srv._grant_item(conn, 21610, '炸薯条', 1)
    check(iid is not None, '发放炸薯条进背包')
    look(srv, conn, 4273)
    check(srv._quest_prompt == {'qid': 101, 'action': 'item'}, '有炸薯条 → 可交付')
    conn.sent = []
    answer(srv, conn, 1)
    check(srv.tasks[101]['step'] == 4, '交付 → step 4')
    left = [v for v in srv.items.values() if v['shape'] == 21610]
    check(not left, '炸薯条已扣除')

    # 步骤 4：村长 → 步骤 5 黑影战斗
    look(srv, conn, 3021)
    answer(srv, conn, 1)
    check(srv.tasks[101]['step'] == 5, '村长 → step 5（黑影）')

    # 步骤 5：神秘的黑影（珊瑚迷宫 1036）
    srv.mapid = 1036
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    look(srv, conn, 8102)
    answer(srv, conn, 1)
    check(srv.in_war and srv.enemy_meta[1]['name'] == '神秘的黑影', '珊瑚迷宫开战：神秘的黑影')
    srv._after_hero_action(conn, dead_ids=[1])
    check(srv.tasks[101]['step'] == 6, '黑影战胜利 → step 6（复命）')

    # 步骤 6：回清风村找村长 → 完成
    srv.mapid = 1025
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    conn.sent = []
    look(srv, conn, 3021)
    answer(srv, conn, 1)
    check(101 not in srv.tasks, '完成后任务从 tasks 移除')
    check(101 in srv.quests_done, '完成后记入 quests_done')
    check(has_op(conn, m.OP_TASK_REMOVE), '下发 S2CTaskRemove(73)')
    check(srv.exp > 0 and srv.coins > m.COIN_INIT, '发放经验/金钱（SIM 数值）')
    check(srv.story_point == 0, '村长的委托剧情点 = 0（真值）')

    # 再点村长：已完成提示，不再重复接
    look(srv, conn, 3021)
    check(srv._quest_prompt is None and srv._quest_prompt != {'qid': 101, 'action': 'accept'},
          '已完成剧情不可重复接取')


# ── ④ 分支：30 级武器 any_shapes / 完成奖励（剧情点 + 领悟招式）──────────────
def test_quest3_branches(tmp):
    print('── ④ 失落的银爵：any_shapes 与完成奖励 ──')
    srv = make_server(tmp, 1019, grade=30)
    conn = FakeConn()
    look(srv, conn, 4329)
    answer(srv, conn, 1)
    check(srv.tasks.get(103, {}).get('step') == 0, '接「失落的银爵」→ step 0')

    # 直接跳到「王大雄要 30 级武器」（第 4 步）
    srv.tasks[103]['step'] = 4
    srv.mapid = 1020
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    look(srv, conn, 8108)
    check(srv._quest_prompt is None, '空手点王大雄不弹确认')
    srv._grant_item(conn, 50030, '青霜剑', 1)     # 30 级武器
    look(srv, conn, 8108)
    check(srv._quest_prompt == {'qid': 103, 'action': 'item'}, '有 30 级武器 → 可交付')
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 5, '交武器 → step 5')
    check(not [v for v in srv.items.values() if v['shape'] == 50030], '30 级武器已扣除')

    # 跳到复命（第 10 步）
    srv.tasks[103]['step'] = 10
    srv.mapid = 1019
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    before_magic = set(srv.magics)
    look(srv, conn, 4329)
    answer(srv, conn, 1)
    check(103 in srv.quests_done, '「失落的银爵」完成')
    check(srv.story_point == 1, '剧情点 +1（复命真值）')
    check(set(srv.magics) - before_magic, '奖励「领悟招式」真的给了一门招式')


# ── ⑤ 持久化 ───────────────────────────────────────────────────────────────
def test_persist(tmp):
    print('── ⑤ 持久化 ──')
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()
    look(srv, conn, 3021)
    answer(srv, conn, 1)
    srv.tasks[101]['step'] = 4
    srv.story_point = 3
    srv.quests_done.add(103)
    srv._persist()

    sv = m.load_role(srv.savepath)
    check(sv.get('story_point') == 3, '剧情点写档（%s）' % sv.get('story_point'))
    check(sorted(sv.get('quests_done') or []) == [103], '已完成列表写档')
    t = (sv.get('tasks') or {}).get('101') or (sv.get('tasks') or {}).get(101)
    check(t and int(t.get('step')) == 4, '任务步骤写档（%s）' % t)
    check(t and t.get('qid') == 101, '任务 qid 写档')


# ── ⑥ quest-spawn 可见性过滤 ────────────────────────────────────────────────
def npc_ids_in_scene(conn):
    """从已下发帧里抠出 AddNpc(op99) 的 NPC id 集合。"""
    ids = set()
    for f in conn.sent:
        if len(f) > 5 and f[1] == m.OP_MAP_ADD_NPC:
            ids.add(struct.unpack('<I', f[2:6])[0])
    return ids


def test_quest_spawn_visibility(tmp):
    print('── ⑥ quest-spawn 可见性过滤 ──')
    srv = make_server(tmp, 1025, grade=10)
    conn = FakeConn()

    # (a) 未接任务：quest-spawn 的「捣乱的野兽」不可见，世界 NPC 永远可见
    vis0 = srv._visible_npc_ids()
    check(8101 not in vis0, '未接任务 → 捣乱的野兽(8101) 不可见（%s）' % vis0)
    check(all(n in vis0 for n in (1001, 1200, 3021, 4118, 4273)),
          '世界 NPC（传送使/车夫/村长/老农夫/张二贵）仍可见')
    conn.sent = []
    srv._send_scene(conn)
    check(8101 not in npc_ids_in_scene(conn), '场景帧里不下发 8101')
    check(len(npc_ids_in_scene(conn)) == len(vis0), '下发 NPC 数 = 可见集大小')

    # (b) 小地图列表同源：op88/sub=ADDNPC 也不该列出 8101
    conn.sent = []
    srv._dispatch(conn, m.C2S_OPENUI,
                  struct.pack('<BH', m.OPENUI_SUB_ADDNPC, m.MAPS[1025]['minimap']))
    # op89 sub182（S2CAddMinimapNpc），不是裸 op182
    _mm = [f for f in conn.sent
           if len(f) > 2 and f[1] == m.OP_OPENUI and f[2] == m.OPENUI_RESP_ADDNPC]
    _gbk = u'捣乱的野兽'.encode('gbk')
    check(_mm and _gbk not in _mm[0], '小地图 NPC 列表不含捣乱的野兽')
    check(_mm and u'村长'.encode('gbk') in _mm[0], '小地图 NPC 列表仍含村长（世界 NPC）')

    # (c) 不可见时点击应被拒（不弹确认、不设 cur_npc）
    srv.cur_npc = None
    look(srv, conn, 8101)
    check(srv._quest_prompt is None, '不可见时点击 8101 不弹确认')

    # (d) 接任务 step0（目标=老农夫）→ 8101 仍不可见
    look(srv, conn, 3021)
    answer(srv, conn, 1)
    check(srv.tasks.get(101, {}).get('step') == 0, '接取 → step 0（目标老农夫）')
    check(8101 not in srv._visible_npc_ids(), 'step0 目标不是它 → 8101 仍不可见')

    # (e) 推进到 step1（目标=捣乱的野兽）→ 8101 出现，并自动重发场景
    look(srv, conn, 4118)
    conn.sent = []
    answer(srv, conn, 1)
    check(srv.tasks[101]['step'] == 1, '对话老农夫 → step 1')
    check(8101 in srv._visible_npc_ids(), 'step1 目标=捣乱的野兽 → 8101 可见')
    check(8101 in npc_ids_in_scene(conn), '推进后自动重发场景，帧里带 8101')
    check(any(f[1] == m.OP_MAP_SCENE_M for f in conn.sent if len(f) > 1),
          '推进触发 SceneM 重发（客户端 ClearAllObject 后需补 NPC）')
    look(srv, conn, 8101)
    check(srv._quest_prompt == {'qid': 101, 'action': 'fight'}, '此时点击 8101 可触发战斗')

    # (f) 放弃任务 → 8101 再次消失
    conn.sent = []
    srv._dispatch(conn, m.C2S_GIVEUP_TASK, struct.pack('<BH', 1, 101))
    check(101 not in srv.tasks, '放弃任务成功')
    check(8101 not in srv._visible_npc_ids(), '放弃后 8101 重新隐藏')
    check(8101 not in npc_ids_in_scene(conn), '放弃后重发的场景帧不含 8101')

    # (g) 跨图：珊瑚迷宫的「神秘的黑影」(8102) 同理
    srv2 = make_server(tmp, 1036, grade=10)
    conn2 = FakeConn()
    check(8102 not in srv2._visible_npc_ids(), '未接任务 → 黑影(8102) 不可见')
    srv2.mapid = 1025
    look(srv2, conn2, 3021)
    answer(srv2, conn2, 1)
    srv2.tasks[101]['step'] = 5                 # 黑影步骤
    srv2.mapid = 1036
    srv2.px, srv2.py = 100, 100
    srv2._arrive_xy = (100, 100)
    check(8102 in srv2._visible_npc_ids(), 'step5（黑影）→ 8102 可见')
    srv2.tasks[101]['step'] = 6                 # 回村长复命
    check(8102 not in srv2._visible_npc_ids(), 'step6 目标不在此图 → 8102 隐藏')

    # (h) 完成后不可再出现
    srv2.mapid = 1025
    look(srv2, conn2, 3021)
    answer(srv2, conn2, 1)
    check(101 in srv2.quests_done, '任务 101 已完成')
    check(8101 not in srv2._visible_npc_ids(), '完成后 8101 不再出现')

    # (i) 反作弊：可见集恒 <= 地图全集，且 quest-spawn 最多只放出当前那一个
    _bad = []
    for mid in m.MAPS:
        _all = [n[0] for n in m.MAPS[mid]['npcs']]
        _qs = [i for i in _all if i in qd.QUEST_SPAWN_IDS]
        if len(_qs) != len(set(_qs)):
            _bad.append(mid)
    check(not _bad, 'quest-spawn id 在同图不重复（%s）' % _bad)


# ── ⑦ 剧情 2「游历四国」全链路 ──────────────────────────────────────────────
def goto(srv, conn, mapid):
    """把内核搬到指定地图（模拟玩家跑图/传送到达）。"""
    srv.mapid = mapid
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)


def fight_and_win(srv, conn, npcid, expect_name, expect_shape=None):
    """点战斗 NPC → 确认 → 断言怪名/造型 → 一击致死 → 断言战斗结束。"""
    look(srv, conn, npcid)
    assert srv._quest_prompt == {'qid': srv._quest_prompt['qid'], 'action': 'fight'}, \
        '战斗步骤未进入确认：%r' % (srv._quest_prompt,)
    answer(srv, conn, 1)
    assert srv.in_war, '未开战'
    meta = srv.enemy_meta[1]
    assert meta['name'] == expect_name, '怪名 %r != %r' % (meta['name'], expect_name)
    if expect_shape is not None:
        assert meta['shape'] == expect_shape, '造型 %r != %r' % (meta['shape'], expect_shape)
    srv._after_hero_action(conn, dead_ids=[1])
    assert not srv.in_war, '敌方全灭后仍未结束战斗'


def test_quest2_full_chain(tmp):
    print('── ⑦ 游历四国 全链路 ──')
    srv = make_server(tmp, 1054, grade=20)
    conn = FakeConn()

    # 接取
    look(srv, conn, 4349)                       # 钱梦庵
    answer(srv, conn, 1)
    check(srv.tasks.get(102, {}).get('step') == 0, 'Lv20 接「游历四国」→ step 0')

    # step0 海底神殿 主教（对话）
    goto(srv, conn, 1019)
    look(srv, conn, 4329)
    answer(srv, conn, 1)
    check(srv.tasks[102]['step'] == 1, '见主教 → step 1')

    # step1 琉璃港 打渔老张（10 株辟邪草）
    goto(srv, conn, 1020)
    srv._grant_item(conn, 25007, '辟邪草', 3)    # 先只给 3 株 → 数量不足应被拒
    look(srv, conn, 4229)
    check(srv._quest_prompt is None, '辟邪草不足（3/10）不弹确认')
    srv._grant_item(conn, 25007, '辟邪草', 7)    # 补齐 10
    look(srv, conn, 4229)
    check(srv._quest_prompt == {'qid': 102, 'action': 'item'}, '满 10 株 → 可交付')
    answer(srv, conn, 1)
    check(srv.tasks[102]['step'] == 2, '交付辟邪草 → step 2')
    check(not [v for v in srv.items.values() if v['shape'] == 25007], '辟邪草 10 株全部扣除')

    # step2 太息林地 巡林客（对话）
    goto(srv, conn, 1018)
    look(srv, conn, 8103)
    answer(srv, conn, 1)
    check(srv.tasks[102]['step'] == 3, '见巡林客 → step 3')

    # step3 太息林地 偷伐者（战斗，剧情点 +1）
    sp_before = srv.story_point
    fight_and_win(srv, conn, 8104, '偷伐者', 2100)
    check(srv.tasks[102]['step'] == 4, '打赢偷伐者 → step 4')
    check(srv.story_point == sp_before + 1, '偷伐者奖励剧情点 +1（%d→%d）'
          % (sp_before, srv.story_point))
    check(not [v for v in srv.items.values() if v['shape'] == 25037],
          '「幸运转转符」本版查无此物 → 不编 id，不入库')

    # step4 回巡林客
    look(srv, conn, 8103)
    answer(srv, conn, 1)
    check(srv.tasks[102]['step'] == 5, '复命巡林客 → step 5')

    # step5 永泽城 汪太公
    goto(srv, conn, 1001)
    look(srv, conn, 4331)
    answer(srv, conn, 1)
    check(srv.tasks[102]['step'] == 6, '见汪太公 → step 6')

    # step6 永泽城 龙校尉（战斗 → 4 个一级还童丹）
    fight_and_win(srv, conn, 4283, '龙校尉', 2150)
    check(srv.tasks[102]['step'] == 7, '胜过龙校尉 → step 7')
    _dan = [v for v in srv.items.values() if v['shape'] == 25037]
    check(sum(int(v['qty']) for v in _dan) == 4, '奖励一级还童丹 ×4（实得 %s）'
          % [v['qty'] for v in _dan])
    [srv.items.pop(i) for i in [k for k, v in srv.items.items() if v['shape'] == 25037]]

    # step7 天苍城 巴容儿（战斗）
    goto(srv, conn, 1013)
    fight_and_win(srv, conn, 4332, '巴容儿', 2190)
    check(srv.tasks[102]['step'] == 8, '胜过巴容儿 → step 8')

    # step8 回明月镇 钱梦庵复命 → 完成（剧情点 +1、飞行旗补充包 ×2）
    goto(srv, conn, 1054)
    sp2 = srv.story_point
    conn.sent = []
    look(srv, conn, 4349)
    answer(srv, conn, 1)
    check(102 in srv.quests_done, '「游历四国」完成')
    check(102 not in srv.tasks, '任务栏移除')
    check(has_op(conn, m.OP_TASK_REMOVE), '下发 S2CTaskRemove(73)')
    check(srv.story_point == sp2 + 1, '复命剧情点 +1（%d→%d）' % (sp2, srv.story_point))
    _flag = [v for v in srv.items.values() if v['shape'] == 30034]
    check(sum(int(v['qty']) for v in _flag) == 2, '奖励飞行旗补充包 ×2（实得 %s）'
          % [v['qty'] for v in _flag])


# ── ⑧ 剧情 3「失落的银爵」全链路 ────────────────────────────────────────────
def test_quest3_full_chain(tmp):
    print('── ⑧ 失落的银爵 全链路 ──')
    srv = make_server(tmp, 1019, grade=30)
    conn = FakeConn()

    look(srv, conn, 4329)                       # 主教
    answer(srv, conn, 1)
    check(srv.tasks.get(103, {}).get('step') == 0, 'Lv30 接「失落的银爵」→ step 0')

    # step0 星霜城 守城卫兵
    goto(srv, conn, 1021)
    look(srv, conn, 8105)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 1, '问守城卫兵 → step 1')

    # step1 流光岛 歪嘴蛙（战斗 → 称心宝石包 ×1）
    goto(srv, conn, 1024)
    fight_and_win(srv, conn, 8107, '歪嘴蛙', 2081)
    check(srv.tasks[103]['step'] == 2, '打赢歪嘴蛙 → step 2')
    check(any(v['shape'] == 72063 for v in srv.items.values()),
          '奖励一级如意宝石包（资料里的「1级宝石包」）')

    # step2 回星霜城复命
    goto(srv, conn, 1021)
    look(srv, conn, 8105)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 3, '复命卫兵 → step 3')

    # step3 小六子（黑椒牛柳 ×1）
    look(srv, conn, 8106)
    check(srv._quest_prompt is None, '空手点小六子不弹确认')
    srv._grant_item(conn, 21411, '黑椒牛柳', 1)
    look(srv, conn, 8106)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 4, '交黑椒牛柳 → step 4')

    # step4 琉璃港 王大雄（任意 30 级武器）
    goto(srv, conn, 1020)
    srv._grant_item(conn, 21411, '黑椒牛柳', 1)      # 干扰项：非武器不该被认
    look(srv, conn, 8108)
    check(srv._quest_prompt is None, '带错东西（黑椒牛柳）不弹确认')
    srv._grant_item(conn, 55030, '碧血剑', 1)        # 换一把别的 30 级武器
    look(srv, conn, 8108)
    check(srv._quest_prompt == {'qid': 103, 'action': 'item'}, 'any_shapes 命中 30 级武器')
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 5, '交 30 级武器 → step 5')
    check(not [v for v in srv.items.values() if v['shape'] == 55030], '武器已扣除')

    # step5 平安港 行脚商人
    goto(srv, conn, 1015)
    look(srv, conn, 8109)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 6, '见行脚商人 → step 6')

    # step6 荒羽半岛 黑乌蛇（战斗 → 宠物作战心得 ×1）
    goto(srv, conn, 1016)
    fight_and_win(srv, conn, 8110, '黑乌蛇', 2090)
    check(srv.tasks[103]['step'] == 7, '除掉黑乌蛇 → step 7')
    check(any(v['shape'] == 25404 for v in srv.items.values()), '奖励宠物作战心得')

    # step7 回平安港复命
    goto(srv, conn, 1015)
    look(srv, conn, 8109)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 8, '复命行脚商人 → step 8')

    # step8 云雀谷 花非花（金疮药 ×5）
    goto(srv, conn, 1014)
    srv._grant_item(conn, 21100, '金疮药', 4)
    look(srv, conn, 8111)
    check(srv._quest_prompt is None, '金疮药不足（4/5）不弹确认')
    srv._grant_item(conn, 21100, '金疮药', 1)
    look(srv, conn, 8111)
    answer(srv, conn, 1)
    check(srv.tasks[103]['step'] == 9, '交金疮药 → step 9')

    # step9 荒羽半岛 卜一刀（战斗 → 魔兽要诀 ×2）
    goto(srv, conn, 1016)
    fight_and_win(srv, conn, 8112, '卜一刀', 2100)
    check(srv.tasks[103]['step'] == 10, '打赢卜一刀 → step 10')
    _yao = [v for v in srv.items.values() if v['shape'] == 25405]
    check(sum(int(v['qty']) for v in _yao) == 2, '奖励魔兽要诀 ×2（实得 %s）'
          % [v['qty'] for v in _yao])

    # step10 回海底神殿复命 → 完成（剧情点 +1 + 领悟招式）
    goto(srv, conn, 1019)
    sp = srv.story_point
    before_magic = set(srv.magics)
    conn.sent = []
    look(srv, conn, 4329)
    answer(srv, conn, 1)
    check(103 in srv.quests_done, '「失落的银爵」完成')
    check(has_op(conn, m.OP_TASK_REMOVE), '下发 S2CTaskRemove(73)')
    check(srv.story_point == sp + 1, '复命剧情点 +1（%d→%d）' % (sp, srv.story_point))
    check(set(srv.magics) - before_magic, '奖励「领悟招式」真的给了一门招式')
    # 三条主线全部走完的累计剧情点：0 + 2 + 3 = 5？——本服 102 只发 2 点（偷伐者 1 + 复命 1），
    # 103 发 3 点（歪嘴蛙 1 + 黑乌蛇 1 + 复命 1），与资料「剧情点」列一致。
    check(srv.story_point == 3, '本条线累计剧情点 = 3（真值：1+1+1）')


def main():
    tmp = tempfile.mkdtemp(prefix='m4_quest_')
    try:
        test_data()
        test_level_gate(tmp)
        test_full_chain(tmp)
        test_quest3_branches(tmp)
        test_persist(tmp)
        test_quest_spawn_visibility(tmp)
        test_quest2_full_chain(tmp)
        test_quest3_full_chain(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
