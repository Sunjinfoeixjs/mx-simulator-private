# -*- coding: utf-8 -*-
"""从录像导出表里抽「主角放招命中」，并对照当前招式伤害模型打印残差。

为什么单做一个脚本
------------------
`m4_server.MAGIC_CALIBRATION_HITS` 是**校准语料**：11 条「主角施放 1101/1301/2101/
2401/2501 → 目标掉多少血」的实测值，用来锚住 `sim-pow-eff-anchored-v1` 的 1 级行为。
这类表最怕「抄完就没人知道怎么复核」——所以这里给出可重跑的唯一来源：

    python tests/sim/calibrate_magic_hits.py            # 打印对照 + 可直接粘贴的表
    python tests/sim/calibrate_magic_hits.py --check    # 与 MAGIC_CALIBRATION_HITS 比对

数据源：`协议资料/报告/录像真值_战斗帧_20261018.csv`（`_mwr_export.py` 的产物）。
抽取口径：同一份录像里 `op=122`（WarPerform）且 `a_id=100`（主角）→ 其后第一条
`op=123`（WState）的 `a_hp_chg`。

⚠ 语料强度（读结论前必看）
  · 这 11 条**全部是 50**，等于同期普攻伤害；5 个招式 pow 各不相同（50/85/80/70/80）
    却打出同一个数 ⇒ 语料对「威力怎么进公式」零辨识力。
  · 这些 .mwr 是 2026-10-15~18 对着**本模拟服**自录的
    （`协议资料/报告/客户端网络层解析_20261018.md` §5.2 明确写了「闭环自证陷阱」）。
  ⇒ 本脚本能证明的只有「当前模型没有偏离本服的历史输出」，**不能**证明原服公式。
"""
from __future__ import print_function

import collections
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import m4_server as m          # noqa: E402

HERO_WAR_ID = m.HERO_WAR_ID


def find_csv():
    """向上找 `协议资料/报告/录像真值_战斗帧_20261018.csv`（不写死绝对路径）。"""
    d = HERE
    for _ in range(6):
        p = os.path.join(d, '协议资料', '报告', '录像真值_战斗帧_20261018.csv')
        if os.path.isfile(p):
            return p
        d = os.path.dirname(d)
    return None


def extract(path):
    """→ {pid: [hp_chg, ...]}（按录像文件名、帧序稳定排序）。"""
    with io.open(path, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    by_file = collections.OrderedDict()
    for r in rows:
        by_file.setdefault(r['file'], []).append(r)
    hits = collections.OrderedDict()
    for fname in sorted(by_file):
        rs = by_file[fname]
        for i, r in enumerate(rs):
            if r['op'] != '122' or r.get('a_id') != str(HERO_WAR_ID):
                continue
            pid = int(r.get('a_pid') or 0)
            for n in rs[i + 1:i + 6]:
                if n['op'] == '123':
                    chg = int(n.get('a_hp_chg') or 0)
                    if chg > 0:
                        hits.setdefault(pid, []).append(chg)
                    break
    return hits


def model_damage(pid):
    """当前模型的 1 级伤害（不建服务器实例：直接复现 _magic_damage 的算式）。

    `_hero_attack_damage()` = ⌊1.3×(A−Deff)⌋ + 力量加点；本服默认加点为 0，
    故这里用 `_default_dmg(...)` 复现基线（与 ATTACK_DMG 快照同口径）。
    """
    slv = 1
    base = m._default_dmg(m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL)
    pow_now = m.magic_pow_at(pid, slv, 0)
    pow_lv1 = m.magic_pow_at(pid, 1, 0)
    eff = m.magic_powrate_at(pid, slv, 0) or 100
    rate = (float(pow_now) / pow_lv1) if pow_lv1 > 0 else 1.0
    mult = max(0.05, min(m.MAGIC_DAMAGE_MAX_MULT, rate * eff / 100.0))
    return max(1, int(base * mult)), base


def main(argv):
    path = find_csv()
    if not path:
        print('✗ 找不到 录像真值_战斗帧_20261018.csv（需要 协议资料/报告/ 那层）')
        return 2
    hits = extract(path)
    if not hits:
        print('✗ 没抽到任何主角放招命中（op122 a_id=%d）' % HERO_WAR_ID)
        return 2

    print('来源：%s' % path)
    print('模型：%s（%s）' % (m.MAGIC_DAMAGE_MODEL, 'SIM / 非原服公式'))
    print('')
    print('%-6s %-10s %-5s %-6s %-5s %-6s %-8s' % (
        '招式', '名称', 'pow', 'eff', '实测', '模型', '残差'))
    worst = 0
    for pid in sorted(hits):
        predicted, base = model_damage(pid)
        name = (m.MAGIC_INFO.get(pid) or {}).get('name', '?')
        for obs in hits[pid]:
            worst = max(worst, abs(predicted - obs))
            print('%-6d %-10s %-5d %-6d %-5d %-6d %+-8d' % (
                pid, name, m.magic_pow_at(pid, 1, 0),
                m.magic_powrate_at(pid, 1, 0), obs, predicted, predicted - obs))
    print('')
    print('普攻基线 = %d；最大单点残差 = %d 点（容差 %d）'
          % (model_damage(sorted(hits)[0])[1], worst,
             m.MAGIC_CALIBRATION_TOLERANCE))
    print('→ 语料全部为 50 且 5 个招式 pow 不同 ⇒ 对「威力进公式」零辨识力；'
          '本表只用于锚住 1 级行为。')
    print('')
    print('可粘贴到 m4_server.MAGIC_CALIBRATION_HITS：')
    print('MAGIC_CALIBRATION_HITS = {')
    for pid in sorted(hits):
        print('    %d: %s,' % (pid, hits[pid]))
    print('}')

    if '--check' in argv:
        want = {int(k): list(v) for k, v in m.MAGIC_CALIBRATION_HITS.items()}
        got = dict(hits)
        same = want == got and worst <= m.MAGIC_CALIBRATION_TOLERANCE
        print('')
        print('%s 与 MAGIC_CALIBRATION_HITS 一致且残差在容差内'
              % ('OK  ' if same else 'FAIL'))
        if not same:
            print('  代码里：%r' % (want,))
            print('  录像里：%r' % (got,))
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
