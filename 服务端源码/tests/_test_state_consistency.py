# -*- coding: utf-8 -*-
"""跨模块状态一致性 —— 回归测试（进程内，假连接，tmp 存档，不起 socket）。

背景（2026-10-23 P0）：`_persist` 的增量快照原是 `dict(data)` **浅拷贝**，而
data 里的 `items` / `tasks` / `hero_res` 是运行时的**活引用**——业务代码全是
原地增删（`self.items[iid] = …` / `del self.items[iid]` / `self.tasks[tid] = …` /
`self.hero_res[name] = …`）。浅拷后「上次快照」与「本次 data」指向同一个对象，
`last[k] != v` 恒为 False：只改背包/任务/战斗资源时 dirty 为空、命中 noop 分支
跳过落盘，**磁盘存档静默停留在旧状态**（实测复现：原地加一件物品后磁盘 items
键数不变；原地改 tasks 后磁盘 tasks 为空）。现有回归没抓到，是因为所有用例要么
整字典替换（`s.items = {…}` 新对象）、要么顺带改了标量键把整份带写。

本测试锁死六组面：
  ① 脏键检测·活引用：原地改 items/tasks/hero_res 必须进 dirty 并落盘
  ② noop 优化不被破坏：真无变化仍跳过写盘（修复不能把增量优化一起修丢）
  ③ detached 快照：_save_snapshot 与运行时不共享对象；脏键都是真实载荷键
  ④ 宠物运行时镜像：实例表 ↔ pet_hp / pet_shape / active_pet_id 在每个
     同步点（切宠/寄存/放生/倒地/用药/GM/存盘）之后自洽
  ⑤ 存档往返：新实例读档后与同步点内存逐字段一致（进程重启等价）
  ⑥ 异步合并：写线程与游戏线程原地修改不赛跑，flush 后磁盘 == 最终内存

用法：python _test_state_consistency.py
"""
from __future__ import print_function

import json
import os
import random
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

import m4_server as m                       # noqa: E402

ok = True


def check(c, msg, detail=''):
    global ok
    if detail and not c:
        msg += '  ← %r' % (detail,)
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))


def make_server(tmp, tag, save_async=False):
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'log_' + tag),
                     savedir=os.path.join(tmp, 'save_' + tag))
    srv._arm_cmd_timer = lambda: None
    srv._save_async = save_async
    if save_async:
        srv._ensure_save_writer()
    return srv


def disk(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────
# ① 脏键检测：活引用字段（items/tasks/hero_res）必须被看见
# ─────────────────────────────────────────────────────────────────────────
def test_dirty_alive_refs(tmp):
    print('── ① 脏键检测：原地改活引用字段必须落盘（2026-10-23 缺陷回归）──')
    srv = make_server(tmp, 'd1')
    srv._persist()                          # 第一次：全量
    w0 = srv._save_writes

    # 原地加一件物品（与 _handle_npc_buy / 掉落 同款 in-place 写法）
    srv.items[9001] = {'shape': 21106, 'key': 0, 'qty': 1, 'pos': 50,
                       'infoext': '回梦散'}
    srv._persist()
    sv = disk(srv.savepath)
    check(srv._save_writes == w0 + 1, '原地加物品 → 真的写盘了（writes %d→%d）'
          % (w0, srv._save_writes))
    check('items' in (srv._save_dirty or []),
          'dirty 含 items（%s）' % (srv._save_dirty,))
    check('9001' in (sv.get('items') or {}),
          '磁盘 items 里有新物品（键 %s）' % sorted(sv.get('items') or {}))

    # 原地消耗一件（del 也是原地）
    del srv.items[9001]
    srv._persist()
    sv = disk(srv.savepath)
    check('9001' not in (sv.get('items') or {}),
          '原地删除也落盘（磁盘已无 9001）')

    # 原地改任务（_quest_accept / 任务推进 同款）
    srv.tasks[101] = {'state': 1, 'progress': 0, 'step': 0, 'qid': 101}
    srv._persist()
    sv = disk(srv.savepath)
    check('tasks' in (srv._save_dirty or []),
          'dirty 含 tasks（%s）' % (srv._save_dirty,))
    check('101' in (sv.get('tasks') or {}),
          '磁盘 tasks 里有新任务（%s）' % sorted(sv.get('tasks') or {}))

    # 原地改战斗资源（_hero_res_cost / GM 同款）
    srv.hero_res['m_MP'] = 3
    srv._persist()
    sv = disk(srv.savepath)
    check('hero_res' in (srv._save_dirty or []),
          'dirty 含 hero_res（%s）' % (srv._save_dirty,))
    check((sv.get('hero_res') or {}).get('m_MP') == 3,
          '磁盘 hero_res.m_MP = 3（%s）' % (sv.get('hero_res') or {}).get('m_MP'))

    # 脏键必须都是本次载荷里真实存在的键（不许有历史残留键名）
    check(set(srv._save_dirty or []) <= set(sv.keys()),
          '脏键全部存在于载荷（dirty=%s）' % (srv._save_dirty,))


def test_noop_preserved(tmp):
    print('── ② noop 优化不被破坏 ──')
    srv = make_server(tmp, 'd2')
    srv._persist()
    w0 = srv._save_writes
    for _ in range(3):
        srv._persist()                      # 什么都没改
    check(srv._save_writes == w0,
          '3 次无变化 persist 一次都没写盘（writes %d→%d）'
          % (w0, srv._save_writes))
    check(srv.save_stats()['noop'] >= 3,
          'noop 计数 >= 3（%d）' % srv.save_stats()['noop'])
    # 深拷贝修复后：无变化仍判定为无变化（快照按值比较，不是按身份）
    srv.px += 1
    srv._persist()
    check(srv._save_writes == w0 + 1, '改一个标量键就写（脏键检测仍灵敏）')


def test_snapshot_detached(tmp):
    print('── ③ detached 快照：与运行时不共享对象 ──')
    srv = make_server(tmp, 'd3')
    srv._persist()
    check(srv._save_snapshot.get('items') is not srv.items
          and srv._save_snapshot.get('tasks') is not srv.tasks
          and srv._save_snapshot.get('hero_res') is not srv.hero_res,
          '_save_snapshot 的三个活引用键均已深拷贝（is not）')
    # 同步模式下：落盘捕获的是 persist 那一刻的状态，之后改内存不回头改文件
    sv1 = disk(srv.savepath)
    srv.items[9002] = {'shape': 1, 'key': 0, 'qty': 1, 'pos': 51,
                       'infoext': 'x'}
    sv2 = disk(srv.savepath)
    check(sv2 == sv1, '未 persist 时文件不被内存原地修改污染')
    srv._persist()
    sv3 = disk(srv.savepath)
    check('9002' in (sv3.get('items') or {}), 'persist 后新状态才进文件')


# ─────────────────────────────────────────────────────────────────────────
# ④ 宠物运行时镜像不变量
# ─────────────────────────────────────────────────────────────────────────
def pet_struct(srv):
    """结构不变量：任何操作后都必须成立（与同步点无关）。返回问题列表。"""
    bad = []
    for pid, p in srv.pets.items():
        if p.get('pid') != pid:
            bad.append('pid 键与实例字段不一致：%r vs %r' % (pid, p.get('pid')))
        if p.get('shape') not in m.PET_CATALOG:
            bad.append('pid=%s 造型不在目录：%r' % (pid, p.get('shape')))
        lv = int(p.get('level') or 0)
        if not (1 <= lv <= m.PET_LEVEL_MAX):
            bad.append('pid=%s 等级越界：%r' % (pid, lv))
        hp, hpmax = int(p.get('hp') or 0), int(p.get('hpmax') or 0)
        if not (0 <= hp <= hpmax):
            bad.append('pid=%s 气血越界：%d/%d' % (pid, hp, hpmax))
        sk = p.get('skills') or []
        if len(set(sk)) != len(sk):
            bad.append('pid=%s 技能重复：%r' % (pid, sk))
    if srv.pets and srv.pet_seq < max(srv.pets):
        bad.append('pet_seq=%d < max(pid)=%d（id 会复用）'
                   % (srv.pet_seq, max(srv.pets)))
    if srv.active_pet_id and srv.active_pet_id not in srv.pets:
        bad.append('active_pet_id=%d 不在实例表' % srv.active_pet_id)
    return bad


def pet_mirror(srv):
    """同步点不变量：出战宠物的实例值必须与运行镜像一致。返回问题列表。"""
    bad = []
    if srv.active_pet_id:
        p = srv.pets.get(srv.active_pet_id)
        if p is None:
            return ['出战宠物缺失']
        if int(p.get('hp') or 0) != int(srv.pet_hp):
            bad.append('hp 分叉：实例 %d vs 镜像 %d'
                       % (p.get('hp'), srv.pet_hp))
        if int(p.get('shape') or 0) != int(srv.pet_shape):
            bad.append('shape 分叉：实例 %d vs 镜像 %d'
                       % (p.get('shape'), srv.pet_shape))
    else:
        if int(srv.pet_hp) != 0:
            bad.append('无出战宠物但 pet_hp=%d' % srv.pet_hp)
    return bad


def _battle_setup(srv, conn):
    """把服务端摆到「战斗中、一只满血出战宠物、一个 9999 血敌人」的姿态。"""
    srv._start_war(conn)
    srv.enemy_hp = {1: 9999}
    srv.enemy_meta = {1: {'spawn_index': 0, 'speed': 1, 'shape': 1, 'name': 'x'}}
    srv.enemy_speed = {1: 1}
    srv._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1, 1: 2}
    srv._pet_alive = True
    srv.pet_passive_mute = True


def test_pet_mirror_chain(tmp):
    print('── ④ 宠物运行时镜像：每个同步点后自洽 ──')
    srv = make_server(tmp, 'd4')
    base = srv._pet()
    check(not pet_struct(srv) and not pet_mirror(srv),
          '初始状态自洽', pet_struct(srv) + pet_mirror(srv))

    # 加第二只 → 切宠：旧宠气血写回、新宠气血读出
    c = FakeConn()
    p2 = srv._add_pet(c, 2081, level=5)
    check(p2 is not None and not pet_struct(srv), '加宠后结构自洽',
          pet_struct(srv))
    srv.pet_hp = 100                      # 旧宠掉到 100（战斗里就是这么掉的）
    srv._set_active_pet(FakeConn(), p2['pid'])
    check(base['hp'] == 100 and srv.pet_hp == p2['hp'],
          '切宠：旧宠写回 100、新宠读出 %d' % srv.pet_hp)
    check(not pet_mirror(srv), '切宠后镜像一致', pet_mirror(srv))

    # 战斗掉血（真实敌方行动路径）→ 同步点后实例跟上
    # 目标在「主角/存活宠物」里按 `battle_rng` 的 enemy_target 流选，
    # 用固定种子 + 有界重试把断言收敛成确定行为；跑完恢复随机源快照。
    # ★ P3-03 起战斗不再走模块级 `random`，所以 `random.seed()` 已经管不着它了 ——
    #   要复现必须 reset 实例随机源（否则这段会变成「运气好才过」的 flaky 测试）。
    srv.pet_hp = srv._pet_hpmax()
    hp_before = srv.pet_hp
    hero_before = srv.hero_hp
    c = FakeConn()
    _battle_setup(srv, c)
    rng_snap = srv.battle_rng.state()
    srv.battle_rng.reset(12345)
    srv.battle_rng.begin_battle()
    try:
        for _ in range(40):
            if srv.pet_hp < hp_before:
                break
            srv._enemy_attack(c, 1)
    finally:
        srv.battle_rng.restore(rng_snap)
    check(srv.pet_hp < hp_before,
          '敌方行动后宠物掉血（%d → %d）' % (hp_before, srv.pet_hp))
    check(srv.hero_hp < hero_before or srv.pet_hp < hp_before,
          '敌方确实造成了伤害')
    check(not pet_struct(srv), '战斗中结构不变量仍成立', pet_struct(srv))
    srv._sync_pet_runtime()
    check(not pet_mirror(srv), '掉血后同步 → 镜像一致', pet_mirror(srv))
    srv.in_war = False

    # 倒地：同步后实例记 0（战斗内死亡路径同款）
    srv.pet_hp = 0
    srv._sync_pet_runtime()
    check(p2['hp'] == 0 and not pet_mirror(srv),
          '倒地写回实例 hp=0', pet_mirror(srv))

    # 药品复活（9B C2SItemUse 定向宠物）
    srv.items[9001] = {'shape': 21106, 'key': 0, 'qty': 1, 'pos': 50,
                       'infoext': ''}
    c = FakeConn()
    srv._handle_item_use(c, struct.pack('<IIB', 9001, p2['pid'], 0))
    check(srv.pet_hp > 0 and p2['hp'] == srv.pet_hp,
          '用药复活：实例与镜像一致（%d）' % srv.pet_hp)
    check(not pet_mirror(srv), '复活后镜像一致', pet_mirror(srv))

    # 寄存出战宠 → 自动收回；取出 → 重新参战
    srv._pet_store(FakeConn(), p2['pid'], True)
    check(srv.active_pet_id == 0 and p2['lookon'] is True
          and not pet_mirror(srv),
          '寄存：收回 + 状态置位 + 镜像自洽', pet_mirror(srv))
    srv._pet_store(FakeConn(), p2['pid'], False)
    srv._set_active_pet(FakeConn(), p2['pid'])
    check(not pet_mirror(srv), '取出重战后镜像一致', pet_mirror(srv))

    # GM 改气血 / 等级：两个入口都必须回写实例
    srv.gm_set_pet_hp(123)
    check(p2['hp'] == 123 and srv.pet_hp == 123 and not pet_mirror(srv),
          'gm_set_pet_hp 回写实例', pet_mirror(srv))
    srv.gm_set_pet_level(20)
    check(p2['level'] == 20 and 0 <= p2['hp'] <= p2['hpmax']
          and not pet_mirror(srv),
          'gm_set_pet_level 后镜像一致（hp %d/%d）' % (p2['hp'], p2['hpmax']),
          pet_mirror(srv))

    # 放生出战宠 → 清空
    srv._pet_free(FakeConn(), p2['pid'])
    check(p2['pid'] not in srv.pets and srv.active_pet_id == 0
          and srv.pet_hp == 0 and not pet_struct(srv) and not pet_mirror(srv),
          '放生出战宠：实例删除、出战清空、镜像归零',
          pet_struct(srv) + pet_mirror(srv))
    check(len(srv.pets) == 1, '剩 1 只（%s）' % sorted(srv.pets))


# ─────────────────────────────────────────────────────────────────────────
# ⑤ 存档往返：进程重启等价
# ─────────────────────────────────────────────────────────────────────────
def test_save_roundtrip(tmp):
    print('── ⑤ 存档往返：读档后与同步点内存逐字段一致 ──')
    srv = make_server(tmp, 'd5')
    p2 = srv._add_pet(FakeConn(), 2060, level=7)
    p2['point'] = 2
    m.pet_add_points(p2, [1, 1, 0, 0, 0])
    m.pet_learn_skill(p2, 1002)
    srv._pet_store(FakeConn(), p2['pid'], True)     # 寄存一只
    srv._set_active_pet(FakeConn(), p2['pid'])      # 先参战（触发绑定）
    srv._pet_store(FakeConn(), p2['pid'], True)     # 再寄存（自动收回）
    srv.pet_hp = 77
    srv._sync_pet_runtime()
    srv._persist()
    sv = disk(srv.savepath)
    check(isinstance(sv.get('pets'), list) and len(sv['pets']) == len(srv.pets),
          '磁盘 pets 条数一致（%d）' % len(sv.get('pets') or []))
    check(sv.get('active_pet_id') == srv.active_pet_id,
          '磁盘 active_pet_id 一致（%s）' % sv.get('active_pet_id'))
    check(sv.get('pet_shape') == srv.pet_shape,
          '磁盘 pet_shape 一致（%s）' % sv.get('pet_shape'))

    # 新实例（进程重启等价）
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_d5b'),
                      savedir=os.path.join(tmp, 'save_d5'))
    check(not pet_struct(srv2) and not pet_mirror(srv2),
          '读档后结构+镜像自洽', pet_struct(srv2) + pet_mirror(srv2))
    check(set(srv2.pets) == set(srv.pets),
          '宠物 id 集合一致 %s' % sorted(srv2.pets))
    check(srv2.active_pet_id == srv.active_pet_id,
          '出战 id 一致（%s）' % srv2.active_pet_id)
    check(srv2.pet_hp == srv.pet_hp and srv2.pet_shape == srv.pet_shape,
          'pet_hp/pet_shape 一致（%d/%d）' % (srv2.pet_hp, srv2.pet_shape))
    q = srv2.pets[p2['pid']]
    check(q['level'] == p2['level'] and q['exp'] == p2['exp']
          and q['skills'] == p2['skills'] and q['lookon'] is True
          and q['hp'] == p2['hp'],
          '逐字段一致（level=%d exp=%d skills=%s lookon=%s hp=%d）'
          % (q['level'], q['exp'], q['skills'], q['lookon'], q['hp']))
    check(bool(q['bound']) == bool(p2['bound']),
          '绑定位一致（%s vs %s）' % (q['bound'], p2['bound']))


# ─────────────────────────────────────────────────────────────────────────
# ⑥ 异步：写线程与游戏线程不赛跑；flush 后磁盘 == 最终内存
# ─────────────────────────────────────────────────────────────────────────
def test_async_no_race(tmp):
    print('── ⑥ 异步合并：原地修改 + 连续 persist，flush 后磁盘==内存 ──')
    srv = make_server(tmp, 'd6', save_async=True)
    srv._persist()
    srv.flush_saves(5.0)
    for i in range(8):                     # 模拟业务链：边改边落盘
        srv.items[8000 + i] = {'shape': 1, 'key': 0, 'qty': 1,
                               'pos': 60 + i, 'infoext': 'x'}
        srv.px = 500 + i
        srv._persist()
    check(srv.flush_saves(5.0) is True, 'flush_saves 完成')
    st = srv.save_stats()
    check(st['pending'] == 0, 'flush 后队列排空（pending=%d）' % st['pending'])
    sv = disk(srv.savepath)
    check({str(k): v for k, v in srv.items.items()} == sv.get('items'),
          '磁盘 items == 最终内存（%d 条）' % len(sv.get('items') or {}))
    check(sv.get('px') == 507, '磁盘 px == 最后一份快照（%s）' % sv.get('px'))
    check(st['writes'] + st['coalesced'] >= 9,
          '8 次业务落盘 + 首次全量都有交代（writes=%d coalesced=%d）'
          % (st['writes'], st['coalesced']))
    srv.stop()


# ─────────────────────────────────────────────────────────────────────────
# ⑦ 任务 / 物品存储完整性（跨模块数据面无脏数据）
# ─────────────────────────────────────────────────────────────────────────
def test_quest_item_integrity(tmp):
    print('── ⑦ 任务/物品存储完整性 ──')
    srv = make_server(tmp, 'd7')
    srv.mapid = 1025
    srv.grade = 10
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', 3021))
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', 1))
    check(101 in srv.tasks, '已接主线 101（真实分派路径）')
    quests = m.questmod.QUESTS
    bad_tid = [tid for tid in srv.tasks if tid not in quests]
    check(not bad_tid, 'tasks 的每个 tid 都是真值表里的剧情 id（%s）' % bad_tid)
    bad_qid = [e.get('qid') for e in srv.tasks.values()
               if e.get('qid') is not None and e.get('qid') not in quests]
    check(not bad_qid, 'task.qid 也都是真值表 id（%s）' % bad_qid)
    check(set(srv.quests_done) <= set(quests),
          'quests_done ⊆ 真值表（%s）' % sorted(srv.quests_done))
    poss = [it.get('pos') for it in srv.items.values()]
    check(len(set(poss)) == len(poss), '背包 pos 无冲突（%d 件）' % len(poss))
    ids = [int(k) for k in srv.items]
    check(len(set(ids)) == len(ids) and srv._iid_seq > max(ids),
          'iid 唯一且 _iid_seq 领先（%d > %d）' % (srv._iid_seq, max(ids)))
    srv._persist()
    sv = disk(srv.savepath)
    check({str(k): v for k, v in srv.items.items()} == sv.get('items'),
          '磁盘 items == 内存（%d 条）' % len(sv.get('items') or {}))
    check({str(k): v for k, v in srv.tasks.items()} == sv.get('tasks'),
          '磁盘 tasks == 内存（%s）' % sorted(sv.get('tasks') or {}))


def main():
    tmp = tempfile.mkdtemp(prefix='m4state_')
    try:
        test_dirty_alive_refs(tmp)
        test_noop_preserved(tmp)
        test_snapshot_detached(tmp)
        test_pet_mirror_chain(tmp)
        test_save_roundtrip(tmp)
        test_async_no_race(tmp)
        test_quest_item_integrity(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = ok is not True
    print('\n=== 状态一致性 %s ===' % ('FAIL' if bad else 'PASS'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
