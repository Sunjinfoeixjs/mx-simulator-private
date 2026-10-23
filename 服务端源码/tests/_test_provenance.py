# -*- coding: utf-8 -*-
"""provenance（出处标注）段 + 本次外置键（shop / 宠物 / 掉落 / 生活 / 收购）契约冒烟。

锁死四件事：
  1. 每个配置键都有出处标注——BATTLE_MAP ∪ PROGRESSION_MAP ∪ 三个段级键，**一个都不许漏**；
  2. 标注值都是非空字符串、不含 None；
  3. default_config() 真的带 provenance 段（与 PROVENANCE 一致），且整份默认配置校验通过；
  4. 新外置的表类段 shop 能**真的**落到 SHOP_ITEMS / SHOP_PRICE / ITEM_NAMES，
     标量键能落到 PET_HP / STR_DMG_DIVISOR / LIFE_LV_MAX 等常量（否则就是「有字段没接线」）。

⚠ 为什么单独一条：JSON 不能写注释，出处标注只能塞进 json 的 provenance 段；
  漏标了不是运行错误，但会让人分不清「真值 / 模拟」，所以用回归钉死覆盖率。
"""
import copy
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import m4_server as m              # noqa: E402
import world_config as wc          # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else ' → %s' % (detail,)))


def main():
    print('[1] PROVENANCE 覆盖全部配置键（漏一个都不行）')
    check('battle 键全覆盖',
          not (set(wc.BATTLE_MAP) - set(wc.PROVENANCE)),
          sorted(set(wc.BATTLE_MAP) - set(wc.PROVENANCE)))
    check('progression 键全覆盖',
          not (set(wc.PROGRESSION_MAP) - set(wc.PROVENANCE)),
          sorted(set(wc.PROGRESSION_MAP) - set(wc.PROVENANCE)))
    check('段级键（world/encounter/shop）全覆盖',
          set(wc.PROVENANCE_SECTIONS) <= set(wc.PROVENANCE),
          sorted(set(wc.PROVENANCE_SECTIONS) - set(wc.PROVENANCE)))
    gaps = wc.provenance_gaps()
    check('可复用覆盖性检查 provenance_gaps() 返回空集', gaps == set(), sorted(gaps))

    print('[2] 标注值都是非空字符串、不含 None')
    bad_val = [k for k, v in wc.PROVENANCE.items()
               if not (isinstance(v, str) and v.strip())]
    check('无非空字符串以外的值', not bad_val, bad_val)
    has_none = [k for k, v in wc.PROVENANCE.items()
                if v is None or 'None' in v]
    check('标注里不含 None', not has_none, has_none)

    print('[3] default_config() 带 provenance 段并与 PROVENANCE 一致')
    cfg = wc.default_config(m)
    check('cfg 含 provenance 段', isinstance(cfg.get('provenance'), dict))
    check('provenance 与 PROVENANCE 逐键一致',
          cfg.get('provenance') == wc.PROVENANCE)
    check('cfg 含 shop 段（18 条）',
          isinstance(cfg.get('shop'), dict)
          and len(cfg['shop'].get('items', [])) == 18,
          len(cfg.get('shop', {}).get('items', [])))
    check('cfg 含新 battle 键', all(k in cfg['battle'] for k in
          ('pet_hp', 'pet_mp', 'pet_fp', 'str_dmg_divisor',
           'drop_chance', 'drop_max_per_battle')))
    check('cfg 含新 progression 键', all(k in cfg['progression'] for k in
          ('tili_max', 'huoli_max', 'life_lv_init', 'life_lv_max',
           'life_exp_init', 'life_exp_max', 'make_cost_huoli', 'make_cost_money',
           'make_cost_sklv', 'sell_ratio_num', 'sell_ratio_den',
           'sell_price_min', 'sell_price_fallback')))

    print('[4] 默认配置校验通过；provenance / shop 作为可选段不误判')
    check('validate(default_config()) 为空', wc.validate(cfg) == [], wc.validate(cfg))
    c_noprov = copy.deepcopy(cfg)
    del c_noprov['provenance']
    check('缺 provenance 段仍通过校验（老配置兼容）',
          wc.validate(c_noprov) == [], wc.validate(c_noprov))
    c_noshop = copy.deepcopy(cfg)
    del c_noshop['shop']
    check('缺 shop 段仍通过校验（老配置兼容）',
          wc.validate(c_noshop) == [], wc.validate(c_noshop))
    c_badprov = copy.deepcopy(cfg)
    c_badprov['provenance'] = {'hero_hp': 123}
    check('provenance 值非字符串 → 报错',
          any('provenance' in e for e in wc.validate(c_badprov)), wc.validate(c_badprov))
    c_badshop = copy.deepcopy(cfg)
    c_badshop['shop'] = {'items': [[22100, '小麦种子']]}
    check('shop.items 结构不对 → 报错',
          any('shop' in e for e in wc.validate(c_badshop)), wc.validate(c_badshop))

    print('[5] apply_to 往返：shop / 标量键真的落到模块常量')
    tmpdir = tempfile.mkdtemp(prefix='m4prov_')
    path = os.path.join(tmpdir, 'world_config.json')
    ok_save, errs = wc.save_config(cfg, path)
    check('save_config 成功', ok_save is True, errs)
    snap_items = list(m.SHOP_ITEMS)
    snap_price = dict(m.SHOP_PRICE)
    snap_names = dict(m.ITEM_NAMES)
    snap_scalar = {k: getattr(m, k) for k in
                   ('PET_HP', 'PET_MP', 'PET_FP', 'STR_DMG_DIVISOR',
                    'DROP_CHANCE', 'DROP_MAX_PER_BATTLE', 'LIFE_LV_MAX',
                    'HERO_TILI_MAX', 'MAKE_COST_MONEY')}
    try:
        applied, msg = wc.apply_to(m, path)
        check('apply 成功', applied is True, msg)
        want = cfg['shop']['items']
        check('SHOP_ITEMS 与配置一致（18 条）',
              [list(t) for t in m.SHOP_ITEMS] == want, len(m.SHOP_ITEMS))
        check('SHOP_PRICE 按新表重建',
              m.SHOP_PRICE == {int(i[0]): int(i[2]) for i in want})
        check('ITEM_NAMES 按新表重建',
              m.ITEM_NAMES == {int(i[0]): i[1] for i in want})
        check('PET_HP 生效', m.PET_HP == cfg['battle']['pet_hp'], m.PET_HP)
        check('STR_DMG_DIVISOR 生效',
              m.STR_DMG_DIVISOR == cfg['battle']['str_dmg_divisor'], m.STR_DMG_DIVISOR)
        check('DROP_CHANCE 生效',
              abs(m.DROP_CHANCE - cfg['battle']['drop_chance']) < 1e-9, m.DROP_CHANCE)
        check('LIFE_LV_MAX 生效',
              m.LIFE_LV_MAX == cfg['progression']['life_lv_max'], m.LIFE_LV_MAX)
        check('HERO_TILI_MAX 生效',
              m.HERO_TILI_MAX == cfg['progression']['tili_max'], m.HERO_TILI_MAX)
        # 派生函数必须跟着新表走（item_name_of 读的是 ITEM_NAMES）
        shape0 = int(want[0][0])
        check('item_name_of 走新名字',
              m.item_name_of(shape0) == want[0][1], m.item_name_of(shape0))
    finally:
        m.SHOP_ITEMS[:] = snap_items
        m.SHOP_PRICE.clear()
        m.SHOP_PRICE.update(snap_price)
        m.ITEM_NAMES.clear()
        m.ITEM_NAMES.update(snap_names)
        for k, v in snap_scalar.items():
            setattr(m, k, v)

    print('[6] 负向：漏标必须能被覆盖性检查报出')
    c = dict(wc.PROVENANCE)
    del c['hero_hp']
    check('删掉 battle 键 → provenance_gaps 报出', wc.provenance_gaps(c) == {'hero_hp'},
          sorted(wc.provenance_gaps(c)))
    c2 = dict(wc.PROVENANCE)
    del c2['grade_max']
    check('删掉 progression 键 → 报出', 'grade_max' in wc.provenance_gaps(c2))
    c3 = dict(wc.PROVENANCE)
    del c3['shop']
    check('删掉段级键 shop → 报出', 'shop' in wc.provenance_gaps(c3))

    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
