# -*- coding: utf-8 -*-
"""物伤公式的边界单测（纯函数，无 socket、无内核）。

守的是「公式语义」而不是「游戏行为」：
  · 两个分支的切换点（Deff == A）必须落在保底上
  · 截断语义必须能复现录像的 50/48/20；四舍五入不能
  · 不隐式 clamp（P > 30·Lt 时允许超过 1.3A）
  · if / max 两种写法在正常分支上完全一致，只在高防御区间分叉
用法： python tests/_test_damage.py
"""
from __future__ import print_function

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import damage_model as dm   # noqa: E402

fails = []


def check(cond, msg):
    print(('OK   ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


# ── A. 有效防御 ──────────────────────────────────────────────────────
check(abs(dm.effective_defense(100, 0, 1) - 100.0) < 1e-9,
      'P=0 → 有效防御 = D')
check(abs(dm.effective_defense(100, 30, 1)) < 1e-9,
      'P=30·Lt → 有效防御 = 0（防御被完全击穿）')
check(abs(dm.effective_defense(100, 60, 1) + 100.0) < 1e-9,
      'P=60·Lt → 有效防御 = −D（公式不 clamp）')
check(abs(dm.effective_defense(100, 15, 1) - 50.0) < 1e-9,
      'P=15·Lt → 有效防御减半')
try:
    dm.effective_defense(100, 0, 0)
    check(False, 'Lt=0 应抛 ValueError（不是静默返回 0）')
except ValueError:
    check(True, 'Lt=0 抛 ValueError（不静默返回 0）')

# ── B. 两个分支的切换点 ─────────────────────────────────────────────
check(dm.physical_damage(40, 0) == 52,
      'D=0 → 1.3×A = 52（A=40）')
check(dm.physical_damage(100, 100) == 10,
      'Deff == A → 保底 0.1A = 10（不能返回 0）')
check(dm.physical_damage(100, 101) == 10,
      'Deff > A → 仍是 0.1A = 10')
check(dm.physical_damage(100, 99) == 1,
      'Deff 略小于 A → 1.3×1 = 1（if 写法；与 max 写法的分叉点）')
check(dm.physical_damage(100, 99, mode='max') == 10,
      '同一组入参 max 写法 → 10（高防御区间被托到保底）')

# ── C. 截断语义：必须能复现录像的 50/48/20 ─────────────────────────
for gap, want in ((39, 50), (37, 48), (16, 20)):
    got = dm.physical_damage(gap, 0)
    check(got == want, 'gap=%d → 伤害 %d（1.3×%d=%.1f 截断）'
          % (gap, want, gap, 1.3 * gap))

round_vals = [dm.physical_damage(g, 0, rounding='round') for g in (39, 37, 16)]
check(round_vals != [50, 48, 20],
      '四舍五入给不出 50/48/20（实得 %r）——所以原函数只能是截断' % (round_vals,))

# ── D. 不隐式 clamp：P 超过 30·Lt 时伤害可超 1.3A ──────────────────
big = dm.physical_damage(10, 10, penetration=61, level=1)
check(big > 13, 'P>30·Lt → 伤害 %d > 1.3A(13)（公式本身不封顶）' % big)

# ── E. 单调性（两种写法在这里分道扬镳）────────────────────────────
mono_max = all(dm.physical_damage(100, d, mode='max')
               >= dm.physical_damage(100, d + 1, mode='max')
               for d in range(0, 200))
check(mono_max, 'max 写法对防御单调不增（D=0..200）')

# ★ 反向还原给的是 if/else，它在 Deff==A 处会「先降到 1、再跳回 10」——
#   防御越高反而挨打更疼。这条路必须找原作者确认（疑似等价于 max 保底）。
jumps = [(d, dm.physical_damage(100, d), dm.physical_damage(100, d + 1))
         for d in range(0, 200)
         if dm.physical_damage(100, d) < dm.physical_damage(100, d + 1)]
check(bool(jumps), 'if 写法在 Deff=A 处不单调：%r —— 待确认是否为 max 语义' % (jumps[:3],))
mono_p = all(dm.physical_damage(100, 50, p) <= dm.physical_damage(100, 50, p + 1)
             for p in range(0, 60))
check(mono_p, '对破防单调不减（P=0..60，Lt=1）')

# ── F. 参数守卫与非负性 ─────────────────────────────────────────────
for bad in (dict(mode='xx'), dict(rounding='xx')):
    try:
        dm.physical_damage(10, 1, **bad)
        check(False, '非法参数 %r 应抛 ValueError' % (bad,))
    except ValueError:
        check(True, '非法参数 %r 抛 ValueError' % (bad,))
check(all(dm.physical_damage(a, d) >= 0
          for a in range(0, 120, 7) for d in range(0, 120, 5)),
      'A/D 在 0..120 网格上伤害恒非负')

# ── G. 反解辅助与正向一致 ───────────────────────────────────────────
ok = True
for dmg in (1, 5, 20, 48, 50, 99):
    lo, hi = dm.damage_gap(dmg)
    ok = ok and (dm.physical_damage(lo + 1e-9, 0) == dmg or
                 dm.physical_damage((lo + hi) / 2, 0) == dmg)
check(ok, 'damage_gap 区间内的 g 能正向还原出同一伤害')

# ── G. 线性模型：(攻−防)/2，来自外部受控变法实验表 ──────────────────
check(dm.physical_damage(100, 0, model='linear') == 50,
      'linear：攻100 防0 → (100−0)/2 = 50（复现录像）')
check(dm.physical_damage(96, 0, model='linear') == 48,
      'linear：攻96 防0 → 48（复现录像）')
check(dm.physical_damage(100, 60, model='linear') == 20,
      'linear：攻100 防60 → 20（复现录像）')
check(dm.physical_damage(40, 90, model='linear') == 0,
      'linear：攻<防 → 0（原表未覆盖负差，按 0 处理）')
check(dm.physical_damage(100, 0, penetration=999, level=1, model='linear') == 50,
      'linear：不使用 penetration / level（与 reverse 的关键区别）')

# ── H. 两个模型各自都能复现 50/48/20 ────────────────────────────────
POINTS = {
    'reverse': dict(hero=(39, 0), pet=(37, 0), foe=(31, 15)),
    'linear': dict(hero=(100, 0), pet=(96, 0), foe=(100, 60)),
}
for model, pts in POINTS.items():
    got = (dm.physical_damage(pts['hero'][0], pts['hero'][1], model=model),
           dm.physical_damage(pts['pet'][0], pts['pet'][1], model=model),
           dm.physical_damage(pts['foe'][0], pts['foe'][1], model=model))
    check(got == (50, 48, 20),
          '%s 模型在其参数点下复现 50/48/20（实得 %r）' % (model, got))

# ── I. 浮动系数（默认必须恒定，保证录像回归零方差）──────────────────
check(dm.rng_factor(1.0, 1.0) == 1.0, 'rng lo==hi → 恒定 1.0')
for i in range(200):
    v = dm.rng_factor(0.95, 1.05, rnd=(lambda k: (lambda: k / 199.0))(i))
    if not (0.95 - 1e-9 <= v <= 1.05 + 1e-9):
        check(False, 'rng 越界：%r' % v)
        break
else:
    check(True, 'rng 在 [0.95,1.05] 内取值（200 个采样点）')

print()
if fails:
    print('=== 物伤公式单测 FAIL（%d 项）===' % len(fails))
    for f in fails:
        print('  - ' + f)
    sys.exit(1)
print('=== 物伤公式单测 PASS ===')
