# -*- coding: utf-8 -*-
"""硬约束 #5 / #16 / #17：现象倒查表对应的代码事实。

这三条来自「现象 → 约束 → 查哪里 → 怎么验」的排查索引（文档 §14.1）。
本测试把它们从**文档断言**变成**代码断言**——文档会过期，代码不会。

  约束 #5  地图全空      → _send_scene 必须三连（SceneM + AddNpc + GotoM）
  约束 #16 战斗菜单不出现 → HERO_WAR_ID 必须非 0
  约束 #17 打了怪没经验   → WarEnd → resync → award_exp 顺序不能乱
"""
import inspect
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


def main():
    # ── 约束 #5：_send_scene 三连 ──
    src = inspect.getsource(m.M4Server._send_scene)
    need = ['build_scene_m', 'build_add_npc', 'build_goto_m']
    miss = [n for n in need if n not in src]
    check('#5 _send_scene 含三连（SceneM + AddNpc + GotoM）',
          not miss, ('缺少 %r' % miss) if miss else '三连齐全')
    # 顺序也要对：SceneM 先，然后 AddNpc（可能多次），最后 GotoM。
    # ★ 不能用 src.find(n) 比位置——它只找**第一次**出现，注入一个更早的
    #   AddNpc 也测不出来。改成提取**所有 sendall 调用的序列**再比对。
    calls = re.findall(r'conn\.sendall\(\s*(build_\w+)', src)
    seq = [c for c in calls if c in ('build_scene_m', 'build_add_npc',
                                     'build_goto_m')]
    ok_order = (seq and seq[0] == 'build_scene_m' and seq[-1] == 'build_goto_m'
                and 'build_add_npc' in seq
                and seq.index('build_scene_m') < seq.index('build_add_npc')
                < seq.index('build_goto_m'))
    check('#5 三连顺序正确（SceneM → AddNpc… → GotoM）',
          bool(ok_order), '实际调用序=%r' % (seq,))

    # ── 约束 #16：HERO_WAR_ID 非 0 ──
    check('#16 HERO_WAR_ID 非 0', m.HERO_WAR_ID != 0,
          'HERO_WAR_ID=%r' % (m.HERO_WAR_ID,))
    # 且不能与敌方 id 空间重叠
    check('#16 主角 id 不与敌方 id 冲突',
          m.HERO_WAR_ID != m.ENEMY_WAR_ID,
          'hero=%r enemy=%r' % (m.HERO_WAR_ID, m.ENEMY_WAR_ID))

    # ── 约束 #17：WarEnd → resync → award_exp 顺序 ──
    #   找含 build_war_end 的方法，检查三个调用点的相对位置
    target = None
    for name in dir(m.M4Server):
        if not name.startswith('_') or name.startswith('__'):
            continue
        try:
            s = inspect.getsource(getattr(m.M4Server, name))
        except (OSError, TypeError):
            continue
        if 'build_war_end' in s and '_award_battle_exp' in s:
            target = (name, s)
            break
    check('#17 找到同时含 WarEnd 与 award_exp 的方法', target is not None,
          target[0] if target else '未找到')
    if target:
        _n, s = target
        p_end = s.find('build_war_end')
        p_resync = s.find('_resync_scene')
        p_exp = s.find('_award_battle_exp')
        check('#17 顺序 WarEnd → resync → award_exp',
              -1 not in (p_end, p_resync, p_exp)
              and p_end < p_resync < p_exp,
              '%s: end=%d resync=%d exp=%d' % (_n, p_end, p_resync, p_exp))
        # 掉落也在之后
        p_drop = s.find('_roll_drops')
        if p_drop >= 0:
            check('#17 掉落也在 resync 之后',
                  p_resync < p_drop, 'resync=%d drop=%d' % (p_resync, p_drop))

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('硬约束 #5/#16/#17：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
