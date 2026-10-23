# -*- coding: utf-8 -*-
"""出手顺序稳定性：7~10 单位阵容 + 同速样本 >= 5 组。

为什么单独一个文件：`_test_battle_speed.py` 验的是「帧序」（要 socket、要真打一场），
本文件验的是**排序函数本身**（进程内、毫秒级），两者互补：
  · 帧序测不出来「同速时谁在前」——那取决于 `units.sort` 的稳定性；
  · 排序测不出「帧有没有真的发出去」——那是前者的事。

关注点（2026-10-19 建立）：
  1. 7/8/9/10 敌的常规速度梯度下，序必须是「主角 -> 宠物 -> 敌 id 升序」的降速排列；
  2. 10 敌时 speed 被 ENEMY_SPEED_MIN 钳到 1，**必然出现同速**（敌9/敌10），
     这组是真实存在的同速样本，不能只在人工构造里测；
  3. 手动把多个敌人设成同速，验证同速段保持**确定**顺序，且 >=5 组重复结果一致。

★ 现状与风险（已记录、未修）：`_speed_order` 靠 `units.sort(key=-speed)` 的**稳定性**
  来定同速次序，而 `units` 的构建顺序来自 `self.enemy_hp.items()` 的**插入顺序**。
  当前所有构建路径都是 `{id: hp for i in range(n)}`（天然有序），所以线上无异常；
  但这是**隐式依赖**——将来若有代码路径乱序重建 enemy_hp（存档合并、GM 加怪），
  同速次序会跟着变。本测试用 `test_insertion_order_sensitivity` 把这个依赖**显式化**，
  一旦有人改成按 id 决胜（更稳），该用例会红并提示更新预期。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def make_server(n_enemies, step=None):
    """造一个已开战的 M4Server，敌数可控。

    step 给定时临时替换 ENEMY_SPEED_STEP（用来构造「全员同速」或「部分同速」）。
    存档/日志都进临时目录，不碰真实进度。
    """
    old_step = m.ENEMY_SPEED_STEP
    if step is not None:
        m.ENEMY_SPEED_STEP = step
    try:
        srv = m.M4Server('127.0.0.1', 0, encounter_every=0,
                         enemies=n_enemies,
                         logdir=tempfile.mkdtemp(prefix='spd_log_'),
                         savedir=tempfile.mkdtemp(prefix='spd_save_'))
        srv.in_war = True
        srv.hero_hp = m.HERO_HP
        srv.enemy_hp = {m.ENEMY_WAR_ID + i: m.ENEMY_HP
                       for i in range(n_enemies)}
        base = m.ENEMY_SPEED_BY_SHAPE.get(m.ENEMY_SHAPE, m.ENEMY_SPEED_BASE)
        srv.enemy_speed_base = base
        sp = [max(m.ENEMY_SPEED_MIN, base - i * m.ENEMY_SPEED_STEP)
              for i in range(n_enemies)]
        srv.enemy_speed = dict(zip(sorted(srv.enemy_hp), sp))
        return srv
    finally:
        m.ENEMY_SPEED_STEP = old_step


def enemy_ids(order):
    """从行动序里剥掉主角/宠物，只留敌方 id。"""
    return [w for w in order if w not in (m.HERO_WAR_ID, m.PET_WAR_ID)]


def main():
    # -- (1) 7~10 单位阵容：常规梯度下的排序 --
    for n in (7, 8, 9, 10):
        srv = make_server(n)
        order = srv._speed_order()
        eids = enemy_ids(order)
        check('%d 敌阵容：敌方按 id 升序（= 速度降序）' % n,
              eids == sorted(eids), 'order=%s' % order)
        check('%d 敌阵容：主角在最前、宠物次之' % n,
              order[0] == m.HERO_WAR_ID and order[1] == m.PET_WAR_ID,
              'order=%s' % order)
        check('%d 敌阵容：%d 个单位不重不漏' % (n, n + 2),
              len(order) == n + 2 and len(set(order)) == n + 2,
              'order=%s' % order)

    # -- (2) 10 敌的真实同速：ENEMY_SPEED_MIN 钳位导致的敌9/敌10 同速 --
    srv = make_server(10)
    speeds = [srv.enemy_speed[w] for w in sorted(srv.enemy_hp)]
    clamped = [w for w in sorted(srv.enemy_hp)
               if srv.enemy_speed[w] == m.ENEMY_SPEED_MIN]
    check('10 敌时出现钳位同速（真实同速样本，非人造）',
          len(clamped) >= 2,
          '钳到 %d 的敌 id=%s，速度表=%s' % (m.ENEMY_SPEED_MIN, clamped, speeds))
    order = srv._speed_order()
    tail = [w for w in order if w in clamped]
    check('钳位同速段的次序 = id 升序', tail == sorted(tail), 'order=%s' % order)

    # -- (3) 同速样本 >=5 组：全员同速 + 部分同速，各跑多轮确认确定性 --
    #   目标是「同一输入重复算，结果必须完全一致」——稳定排序的核心承诺。
    same_speed_cases = []
    for n in (7, 8, 9, 10):
        # 每档 1 组全员同速 + 1 组部分同速 = 8 组，满足 >=5
        srv = make_server(n, step=0)          # step=0 → 全员同速
        same_speed_cases.append(('%d 敌全员同速' % n, srv, 50))
        srv2 = make_server(n)
        # 部分同速：把前一半敌人改成同一速度
        ids = sorted(srv2.enemy_hp)
        for w in ids[:max(2, n // 2)]:
            srv2.enemy_speed[w] = 33
        same_speed_cases.append(('%d 敌部分同速(前 %d 个)' % (n, max(2, n // 2)), srv2, 33))

    check('同速样本组数 >= 5', len(same_speed_cases) >= 5,
          '实得 %d 组：%s' % (len(same_speed_cases),
                            '、'.join(c[0] for c in same_speed_cases)))

    nondet = []
    for label, srv, sp in same_speed_cases:
        # 同速段：速度 == sp 的敌方 id（按出现序）
        def seg(o):
            return [w for w in o if w in srv.enemy_hp
                    and srv.enemy_speed.get(w) == sp]
        first = seg(srv._speed_order())
        # 重复算 20 次，必须完全一致
        runs = {tuple(seg(srv._speed_order())) for _ in range(20)}
        if len(runs) != 1:
            nondet.append('%s → %d 种不同结果' % (label, len(runs)))
    check('同速场景重复 20 次结果完全确定', not nondet,
          '；'.join(nondet) if nondet else '全部一致')

    # -- (4) 插入顺序**不再**影响结果（2026-10-20 改显式三元键）--
    #   旧实现靠 units.sort 的稳定性 + enemy_hp 插入序定同速次序，是个隐式契约。
    #   现在 key = (-speed, side_rank, spawn_index)，spawn_index 在 _start_war 固定。
    #   本用例故意乱序插入，断言结果**与插入序无关**——这正是改动的目的。
    srv = make_server(8)
    shuffled = [5, 4, 3, 2, 1, 6, 7, 8]
    srv.enemy_hp = {w: m.ENEMY_HP for w in shuffled}
    srv.enemy_speed = {w: 50 for w in shuffled}     # 全员同速
    srv._spawn_index = {w: i for i, w in enumerate(sorted(srv.enemy_hp))}
    got = enemy_ids(srv._speed_order())
    check('插入顺序**不影响**同速次序（显式 spawn_index 生效）',
          got == sorted(shuffled),
          '乱序插入 %s → 排序 %s（应恒为 id 升序 %s）'
          % (shuffled, got, sorted(shuffled)))

    # 同一份数据换个插入序再跑一次，结果必须一模一样
    srv2 = make_server(8)
    srv2.enemy_hp = {w: m.ENEMY_HP for w in sorted(shuffled)}
    srv2.enemy_speed = {w: 50 for w in shuffled}
    srv2._spawn_index = {w: i for i, w in enumerate(sorted(srv2.enemy_hp))}
    got2 = enemy_ids(srv2._speed_order())
    check('两种插入序得到同一结果（可复现性）',
          got == got2, '乱序 %s vs 顺序 %s' % (got, got2))

    # ── side_rank 必须真正参与决胜（否则和「只按 spawn_index」无法区分）──
    #   ★ 关键设计：spawn_index 按 主角0/宠物1/敌方2+ 分配，所以正常情况下
    #     side_rank 与 spawn_index **恰好同序**，去掉 side_rank 也测不出来。
    #     必须构造「spawn_index 与 side_rank 相反」的场景才能验证它真起作用：
    #     把敌方 spawn_index 全设成 0（比宠物 1 还小），此时若只按 spawn_index，
    #     敌方会排到宠物前；正确实现应按 side_rank 把宠物排前面。
    srv3 = make_server(3)
    srv3._pet_alive = True
    srv3.enemy_speed = {w: m.PET_SPEED for w in srv3.enemy_hp}
    srv3._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1}
    for w in sorted(srv3.enemy_hp):
        srv3._spawn_index[w] = 0        # 故意小于宠物，与 side_rank 相反
    order3 = srv3._speed_order()
    check('side_rank 真正参与决胜（spawn_index 相反时仍我方优先）',
          order3.index(m.PET_WAR_ID) < order3.index(m.ENEMY_WAR_ID),
          '宠物 idx=%d 敌方 idx=%d（若相反说明只用了 spawn_index）'
          % (order3.index(m.PET_WAR_ID), order3.index(m.ENEMY_WAR_ID)))

    # ── spawn_index 不能退化成 uid ──
    #   ★ 上面几个用例里 uid 恰好与入场序一致（敌 id 1..N 按序生成），
    #     所以「用 uid 代替 spawn_index」测不出来。这里构造**相反**的场景：
    #     入场序 3→1→2，但 uid 是 1/2/3。若实现用 uid 决胜会得 1,2,3；
    #     正确实现按 spawn_index 应得 3,1,2。
    srv4 = make_server(3)
    srv4._pet_alive = False
    srv4.enemy_hp = {w: m.ENEMY_HP for w in (1, 2, 3)}
    srv4.enemy_speed = {w: 50 for w in (1, 2, 3)}      # 全员同速
    srv4._spawn_index = {1: 1, 2: 2, 3: 0}            # 入场序 3,1,2（与 uid 相反）
    got4 = enemy_ids(srv4._speed_order())
    check('spawn_index 独立于 uid（入场序与 id 相反时按入场序）',
          got4 == [3, 1, 2],
          'spawn_index={1:1,2:2,3:0} → 排序 %s（应为 [3,1,2]；若得 [1,2,3] 说明用了 uid）'
          % (got4,))

    # ── enemy_meta：spawn_index 是事实字段，不是从 dict 序推算 ──
    #   ★ 决定性用例：dict 插入序升序、spawn_index 逆序。
    #     若实现依赖 dict 序 / sorted(id)，会得 [1,2,3,4,5]；
    #     正确实现读 enemy_meta 的 spawn_index，得 [5,4,3,2,1]。
    srv5 = make_server(5)
    srv5._pet_alive = False
    srv5.enemy_meta = {}
    #   速度与 spawn_index 反相关（第 0 只最快），否则速度降序会把 spawn 序掩盖掉
    for wid, spi in zip([1, 2, 3, 4, 5], [4, 3, 2, 1, 0]):
        srv5.enemy_meta[wid] = {'spawn_index': spi,
                                'speed': 20 + (4 - spi) * 5,
                                'shape': m.ENEMY_SHAPE, 'name': 'x'}
    srv5.enemy_hp = {w: m.ENEMY_HP for w in srv5.enemy_meta}
    srv5.enemy_speed = {w: mt['speed'] for w, mt in srv5.enemy_meta.items()}
    srv5._spawn_index = {m.HERO_WAR_ID: 0}
    for w, mt in srv5.enemy_meta.items():
        srv5._spawn_index[w] = 2 + mt['spawn_index']
    check('_enemy_ids 按 spawn_index 而非 dict 插入序',
          srv5._enemy_ids() == [5, 4, 3, 2, 1],
          '插入 [1,2,3,4,5] / spawn_index 逆序 → %s（应为 [5,4,3,2,1]）'
          % (srv5._enemy_ids(),))
    check('行动序同样按 spawn_index',
          srv5._speed_order() == [100, 5, 4, 3, 2, 1],
          'order=%s' % (srv5._speed_order(),))

    # enemy_meta 里每只都带齐四个字段（spawn_index/speed/shape/name）
    srv6 = make_server(3)
    srv6._pet_alive = False
    srv6.enemy_meta = {}
    for i in range(3):
        srv6.enemy_meta[m.ENEMY_WAR_ID + i] = {
            'spawn_index': i, 'speed': 40 - i * 5,
            'shape': m.ENEMY_SHAPE, 'name': m.ENEMY_NAME}
    check('enemy_meta 每条字段齐全',
          all(set(mt) >= {'spawn_index', 'speed', 'shape', 'name'}
              for mt in srv6.enemy_meta.values()),
          'keys=%s' % [sorted(mt) for mt in srv6.enemy_meta.values()][:1])
    check('spawn_index 连续且从 0 起',
          sorted(mt['spawn_index'] for mt in srv6.enemy_meta.values())
          == list(range(3)),
          'got=%s' % sorted(mt['spawn_index'] for mt in srv6.enemy_meta.values()))

    # ── enemy_hp 构造顺序打乱：完整排序结果必须不变 ──
    #   这是比用例 (4) 更强的保证：不只是「同速段次序不变」，而是**整个行动序**
    #   （含速度梯度部分）都与 dict 构造顺序无关。
    #
    #   做法：固定每只的 spawn_index（入场序是事实，不该随构造顺序变），
    #   只打乱 enemy_hp / enemy_meta 的**插入顺序**，穷举全部排列。
    #   5 敌 = 120 种排列，全覆盖；10 敌 = 随机抽 30 种（含钳位同速段）。
    def _build_with_order(srv, order, spi, base_speed, step):
        srv.enemy_meta = {}
        for w in order:
            srv.enemy_meta[w] = {
                'spawn_index': spi[w],
                'speed': max(m.ENEMY_SPEED_MIN, base_speed - spi[w] * step),
                'shape': m.ENEMY_SHAPE, 'name': 'x'}
        srv.enemy_hp = {w: m.ENEMY_HP for w in srv.enemy_meta}
        srv.enemy_speed = {w: mt['speed'] for w, mt in srv.enemy_meta.items()}
        srv._spawn_index = {m.HERO_WAR_ID: 0, m.PET_WAR_ID: 1}
        for w, mt in srv.enemy_meta.items():
            srv._spawn_index[w] = 2 + mt['spawn_index']
        return (srv._enemy_ids(), srv._speed_order())

    import itertools as _it
    import random as _rnd

    # (a) 5 敌 · 非同速梯度 · 穷举 120 种排列
    ids5 = [1, 2, 3, 4, 5]
    spi5 = {w: i for i, w in enumerate(ids5)}
    srv7 = make_server(5)
    srv7._pet_alive = True
    base5 = _build_with_order(srv7, ids5, spi5, 40, 5)
    bad5 = []
    for perm in _it.permutations(ids5):
        got = _build_with_order(srv7, list(perm), spi5, 40, 5)
        if got != base5:
            bad5.append((perm, got))
    check('5 敌·梯度：穷举 120 种构造序，结果全一致',
          not bad5,
          '不一致 %d 种%s' % (len(bad5),
                              ('，首个 %r→%r' % (bad5[0][0], bad5[0][1])) if bad5 else ''))

    # (b) 5 敌 · 全员同速 · 穷举 120 种排列（同速最依赖决胜键）
    srv8 = make_server(5)
    srv8._pet_alive = True
    base5s = _build_with_order(srv8, ids5, spi5, 50, 0)
    bad5s = []
    for perm in _it.permutations(ids5):
        got = _build_with_order(srv8, list(perm), spi5, 50, 0)
        if got != base5s:
            bad5s.append((perm, got))
    check('5 敌·全员同速：穷举 120 种构造序，结果全一致',
          not bad5s,
          '不一致 %d 种%s' % (len(bad5s),
                              ('，首个 %r→%r' % (bad5s[0][0], bad5s[0][1])) if bad5s else ''))

    # (c) 10 敌 · 含 ENEMY_SPEED_MIN 钳位同速段 · 随机抽 30 种排列
    ids10 = list(range(1, 11))
    spi10 = {w: i for i, w in enumerate(ids10)}
    srv9 = make_server(10)
    srv9._pet_alive = True
    base10 = _build_with_order(srv9, ids10, spi10, 40, 5)
    _rnd.seed(20261020)
    bad10 = []
    for _ in range(30):
        perm = ids10[:]
        _rnd.shuffle(perm)
        got = _build_with_order(srv9, perm, spi10, 40, 5)
        if got != base10:
            bad10.append((tuple(perm), got))
    check('10 敌·含钳位同速：随机 30 种构造序，结果全一致',
          not bad10,
          '不一致 %d 种%s' % (len(bad10),
                              ('，首个 %r→%r' % (bad10[0][0], bad10[0][1])) if bad10 else ''))

    # -- 汇总 --
    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('出手顺序稳定性：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        print('失败项：')
        for nm, ok in results:
            if not ok:
                print('  ✗ %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
