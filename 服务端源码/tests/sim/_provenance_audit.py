# -*- coding: utf-8 -*-
"""_provenance_audit.py — 反向审计 provenance 的「真值声明」是否与证据一致。

与 `tests/_test_provenance.py` 的分工：
  * 那个查**覆盖率**（每个配置键都有标注，一个都不许漏）；
  * 本脚本查**准确性**：已作废的断言不许再出现，关键键必须写清证据边界。

审计项：
  1. 已推翻断言黑名单（字符串级）：
       · 「客户端 grade 封顶 120」——m_Grade/m_GradeLimit 实为 TBYTE，资料库 140/150；
       · 「野狼必红 / 必不红」——旧统计基于 LSB/MSB 拆位错误 + 语料混族，已作废；
       · 「生产品价格真值」——milldata.maxprice 是工厂自定价上限，不是 NPC 售价；
       · 「半真值（录像）」标注在 enemy_hp 上——14 场 WAR_DEL 累计伤害 50/98/146/148，
         不能反推固定 HP。
  2. live `world_config.json` 的 provenance 段必须与代码 `PROVENANCE` **逐键相等**
     （JSON 不能写注释，漂移只能靠这里挡住）。
  3. `petzz[4005]` 基线 sanity：`PET_ABILITY` 五项与种族基线的比值须落在 0.8~1.2，
     `PET_LIFE` 须小于寿命基线 `lift`（8700 < 15000）。
  4. `CRAFT_DATA`：48 条产物必须全部自带 lv(5..120)/cost(16..58)——这是
     `make_cost_huoli`/`make_cost_sklv` 标「兜底、不触发」的前提。

用法：python _provenance_audit.py
退出码：0 = 全部通过；1 = 有红。
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import m4_server as m          # noqa: E402
import world_config as wc      # noqa: E402

PETZZ = os.path.abspath(os.path.join(
    HERE, '..', '..', '客户端源码', 'src', 'data', 'petzz.py'))
CONFIG_JSON = os.path.join(HERE, 'world_config.json')

# 黑名单：(键或'*', 作废的子串, 为什么作废, 豁免子串)
# 豁免用于区分「断言它」与「否定它」——新标注里必须能写「不是野狼必红」这类澄清。
FORBIDDEN = (
    ('*', '封顶 120', 'm_Grade/m_GradeLimit 为 TBYTE(0..255)，资料库上限 140/150', ()),
    ('*', 'grade 封顶 120', '同上', ()),
    ('*', '必红', '会心旧统计基于 LSB/MSB 拆位错误 + 语料混族，已作废', ('不是', '不构成', '不成立')),
    ('*', '生产品价格真值', 'milldata.maxprice 是工厂自定价上限，非 NPC 售价', ()),
)

# 必须出现的证据边界：(键, 必含子串, 为什么)
REQUIRED = (
    ('grade_max', '140', '飞升前上限'),
    ('grade_max', '150', '飞升后上限'),
    ('enemy_hp', 'WAR_DEL', '录像只能给出致死前累计伤害，不能反推固定 HP'),
    ('enemy_hp', '模拟', 'enemy_hp 不是半真值'),
    ('shop', 'milldata', '明确 milldata 价格字段的语义边界'),
    ('make_cost_huoli', '兜底', '说明 10 只是 craftdata 缺 cost 的兜底'),
    ('make_cost_sklv', '兜底', '说明 1 只是 craftdata 缺 lv 的兜底'),
    ('life_lv_max', 'craftdata', '120 = craftdata 最高配方 lv，不是客户端等级上限'),
)

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else ' => %s' % (detail,)))


def load_petzz_shape4005():
    src = io.open(PETZZ, encoding='utf-8', errors='replace').read()
    mobj = re.search(r'4005\s*:\s*\(([^)]*)\)', src)
    if not mobj:
        raise RuntimeError('petzz 中找不到 4005 行')
    vals = tuple(int(x.strip()) for x in mobj.group(1).split(',') if x.strip())
    if len(vals) != 7:
        raise RuntimeError('petzz[4005] 不是 7 元组: %r' % (vals,))
    return vals  # (mtype, tz, fz, gz, dz, sz, lift)


def main():
    prov = wc.PROVENANCE

    print('[1] 已推翻断言黑名单')
    for key, bad, why, allow in FORBIDDEN:
        def _hit(v):
            return bad in v and not any(a in v for a in allow)

        if key == '*':
            hits = [k for k, v in prov.items() if _hit(v)]
            check('无 "%s"（%s）' % (bad, why), not hits, hits)
        else:
            v = prov.get(key, '')
            check('%s 无 "%s"（%s）' % (key, bad, why), not _hit(v), v)

    print('[2] 关键键证据边界')
    for key, sub, why in REQUIRED:
        v = prov.get(key, '')
        check('%s 含 "%s"（%s）' % (key, sub, why), sub in v, v)

    print('[3] live world_config.json 与代码 PROVENANCE 同步')
    try:
        with io.open(CONFIG_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        check('world_config.json 可读', False, e)
    else:
        jprov = cfg.get('provenance') or {}
        diff = {k: (jprov.get(k), v) for k, v in prov.items()
                if jprov.get(k) != v}
        extra = sorted(set(jprov) - set(prov))
        check('provenance 与代码一致', not diff, sorted(diff)[:5])
        check('无多余 provenance 键', not extra, extra)

    print('[4] petzz[4005] 基线 sanity（petzz 只用于客户端评级，不参与战斗公式）')
    try:
        _mtype, tz, fz, gz, dz, sz, lift = load_petzz_shape4005()
    except RuntimeError as e:
        check('petzz[4005] 可解析', False, e)
    else:
        base = (gz, dz, tz, fz, sz)   # 攻击/防御/体力/法力/速度
        ratios = [a / float(b) for a, b in zip(m.PET_ABILITY, base)]
        check('PET_ABILITY 五项与基线比值 0.8~1.2', all(0.8 <= r <= 1.2 for r in ratios),
              ['%.3f' % r for r in ratios])
        check('PET_LIFE < 寿命基线 lift', m.PET_LIFE < lift,
              '%s vs %s' % (m.PET_LIFE, lift))

    print('[5] CRAFT_DATA 全自带 lv/cost（make_cost_* 兜底不触发的前提）')
    data = m.CRAFT_DATA
    check('CRAFT_DATA 条目数 48', len(data) == 48, len(data))
    lvs = [d.get('lv') for d in data.values()]
    costs = [d.get('cost') for d in data.values()]
    check('craftdata lv 全在 5..120', all(x is not None and 5 <= x <= 120 for x in lvs),
          sorted(set(lvs))[:5])
    check('craftdata cost 全在 16..58', all(x is not None and 16 <= x <= 58 for x in costs),
          sorted(set(costs))[:5])

    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未通过: %s' % bad)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
