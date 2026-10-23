# -*- coding: utf-8 -*-
"""物伤公式的「参数可行域」求解器（只读数据，不改游戏）。

输入：协议资料/报告/录像真值_战斗帧_20261018.csv（WarWState(123) 的 hp_chg）
输出：
  ① 观测到的伤害分布（按受击单位归类）
  ② 给定 gain=1.3 截断语义时，每个伤害对应的「A − Deff」可行区间与整数解
  ③ **gain 本身的可行区间**——用来判断「1.3」是被数据支持，还是只是不冲突
  ④ 给定 (A, Lt, P) 时反解出的 D —— 也就是真正的参数可行域示例
  ⑤ 判别力结论：if/max 两种写法、P 的两种含义，现有数据能否区分

用法： python _damage_feasible.py
"""
from __future__ import print_function

import collections
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(os.path.dirname(os.path.dirname(HERE)), '服务端源码', 'sim')
sys.path.insert(0, SIM)
import damage_model as dm   # noqa: E402

CSV_PATH = os.path.join(HERE, '录像真值_战斗帧_20261018.csv')
GAIN_GRID = [round(0.50 + 0.001 * i, 3) for i in range(2500)]


def load():
    rows = list(csv.DictReader(io.open(CSV_PATH, encoding='utf-8-sig')))
    units = {}
    for r in rows:
        if r['op'] == '115' and (r['a_uid'] or '').strip():
            units[r['a_uid'].strip()] = {
                'name': (r['a_name'] or '').strip(),
                'allied': (r['a_allied'] or '').strip(),
                'shape': (r['a_shape'] or '').strip(),
            }
    states = []
    for r in rows:
        if r['op'] != '123':
            continue
        chg = (r['a_hp_chg'] or '').strip()
        if not chg:
            continue
        # ★ 战斗帧 csv 里 WState 的单位 id 存的是 a_uid（不是 a_id），与 WarAdd 同列
        uid = (r['a_uid'] or '').strip()
        states.append({
            'file': r['file'],
            'id': uid,
            'chg': int(chg),
            'ward': (r['a_ward'] or '').strip(),
            'hurt': (r['a_hurt'] or '').strip(),
            'unit': units.get(uid, {}),
        })
    return states


def main():
    st = load()
    print('=' * 72)
    print('① 观测真值：WState(123) 共 %d 条' % len(st))
    print('=' * 72)
    by_dmg = collections.Counter(s['chg'] for s in st)
    print('伤害分布：%s' % by_dmg.most_common())
    print('ward 分布：%s   hurt 分布：%s'
          % (collections.Counter(s['ward'] for s in st).most_common(),
             collections.Counter(s['hurt'] for s in st).most_common()))
    per_unit = collections.defaultdict(list)
    for s in st:
        u = s['unit']
        per_unit[(s['id'], u.get('name') or '?', u.get('allied') or '?')].append(s['chg'])
    for (uid, name, allied), vals in sorted(per_unit.items(), key=lambda kv: -len(kv[1])):
        print('  受击 id=%-4s %-8s allied=%s  n=%-2d  伤害=%s'
              % (uid, name, allied, len(vals), sorted(set(vals))))

    print()
    print('=' * 72)
    print('② gain=1.3 + 截断：每个伤害对应的 gap = A − Deff')
    print('=' * 72)
    gaps = {}
    for dmg in sorted(by_dmg):
        lo, hi = dm.damage_gap(dmg)
        ints = [g for g in range(1, 256) if lo <= g < hi]
        gaps[dmg] = ints
        print('  伤害 %-3d → gap ∈ [%.3f, %.3f)  整数解 %s' % (dmg, lo, hi, ints))
    print('  唯一性检查：')
    for dmg, ints in sorted(gaps.items()):
        g = ints[0] if ints else None
        if g is None:
            print('    伤害 %d 无整数解（1.3 与截断不兼容）' % dmg)
            continue
        print('    %d ← gap %d（邻值：%d→%d，%d→%d）'
              % (dmg, g, g - 1, int(1.3 * (g - 1)), g + 1, int(1.3 * (g + 1))))

    print()
    print('=' * 72)
    print('③ gain 本身的可辨识性：哪些 gain 能同时产出 50/48/20')
    print('=' * 72)
    ok_gains = []
    for k in GAIN_GRID:
        if all(any(int(k * g) == d for g in range(1, 256)) for d in by_dmg):
            ok_gains.append(k)
    if ok_gains:
        runs = []
        s = prev = ok_gains[0]
        for k in ok_gains[1:]:
            if abs(k - prev - 0.001) < 1e-9:
                prev = k
                continue
            runs.append((s, prev))
            s = prev = k
        runs.append((s, prev))
        print('  可行区间共 %d 段（网格 0.001，扫描 0.50~3.00）：' % len(runs))
        for a, b in runs:
            print('    [%.3f, %.3f]' % (a, b))
        print('  1.3 在区间内：%s' % ('是' if 1.3 in ok_gains else '否'))
        print('  → 结论：数据只能约束「gain × gap」，**无法唯一确定 gain**；')
        print('    1.3 来自反向还原，不是从这 47 条样本拟合出来的。')
        # 反过来：若接受 1.3 推出的 gap 分配（50←39 / 48←37 / 20←16），
        # 则 gain 被夹在这三段的交集里——这是「条件可行域」。
        lo = max(float(d) / g for d, g in ((50, 39), (48, 37), (20, 16)))
        hi = min(float(d + 1) / g for d, g in ((50, 39), (48, 37), (20, 16)))
        print('  条件可行域：固定 gap=(39,37,16) 时 gain ∈ [%.4f, %.4f)'
              % (lo, hi))
        print('    （1.3 落在其中；偏离超过 ±0.5% 就会有一个样本对不上）')

    print()
    print('=' * 72)
    print('④ 参数可行域示例：已知 A/Lt/P 时反解 D（gap = A − Deff）')
    print('=' * 72)
    print('  Deff = A − gap；D = Deff / (1 − P/(30·Lt))')
    print('  %-6s %-5s %-6s | %-13s %-13s %-13s'
          % ('A', 'Lt', 'P', '主角打怪(gap39)', '宠物打怪(gap37)', '怪打人(gap16)'))
    for A in (40, 60, 100):
        for Lt in (1, 10, 50):
            for P in (0, 10):
                row = []
                for gap in (39, 37, 16):
                    k = 1.0 - float(P) / (30.0 * Lt)
                    deff = A - gap
                    row.append('%.1f' % (deff / k) if k > 0 else '∞')
                print('  %-6d %-5d %-6d | %-12s %-12s %-12s'
                      % (A, Lt, P, row[0], row[1], row[2]))

    print()
    print('=' * 72)
    print('⑤ 判别力：现有数据能区分什么、不能区分什么')
    print('=' * 72)
    same = all(dm.physical_damage(g, 0) == dm.physical_damage(g, 0, mode='max')
               for g in (39, 37, 16))
    print('  · if / max 两种写法：三个样本%s'
          % ('结果完全一致 → **判别不了**' if same else '有差异'))
    print('  · P 是「破防」还是「攻方等级」：样本里没有跨等级/跨破防的对照 → **判别不了**')
    print('  · 0.1A 保底分支：47 条全是正常分支（最低伤害 20）→ **零样本**')
    print('  · Lt 是攻方还是守方等级：同上，无对照 → **判别不了**')
    print('  · 取整方式：四舍五入给不出 50/48/20（[51,48,21]）→ **可判定为截断**')

    print()
    print('结论：公式形态可信、常数 1.3 与三个伤害自洽；但 (A,D,P,Lt) 四元组')
    print('      在这批数据上**严重欠定**——需要「同一角色打不同等级/不同防御目标」')
    print('      的对照样本才能收紧。在此之前，接入只能当参数化模型 + 录像回归兜底。')


if __name__ == '__main__':
    main()
