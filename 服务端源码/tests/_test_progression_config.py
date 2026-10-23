# -*- coding: utf-8 -*-
"""progression 段（经验/金钱曲线，全部 ★SIM）配置契约冒烟。

锁死四件事：
  1. 默认配置里带 progression 段，12 个键齐全且与模块常量一致
  2. 改配置能**真的**改到 m4_server 常量（否则就是「有字段没接线」）
  3. 旧配置（没有 progression 段）**不报错**，且保留模块默认
     —— 这条很关键：如果缺段就整体判无效，60 张地图会一起丢掉
  4. 非法值（越界/非整数/未知字段）必须报错，不能静默吞掉

⚠ progression 全部是模拟参数，没有真值来源（见 world_config.PROGRESSION_MAP
  上方三条证据）。本测试只保证「可配置 + 不误判」，不声称真值。
"""
import copy
import io
import json
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
import world_config as W           # noqa: E402

fails = []


def check(name, cond, extra=''):
    print(('  ok   ' if cond else '  FAIL ') + name + ('' if cond else ' → %s' % (extra,)))
    if not cond:
        fails.append(name)


def main():
    cfg = W.default_config(m)

    print('[1] 默认配置含 progression，且与模块常量一致')
    check('有 progression 段', isinstance(cfg.get('progression'), dict))
    prog = cfg['progression']
    check('12 个键齐全', len(prog) == len(W.PROGRESSION_MAP),
          (len(prog), len(W.PROGRESSION_MAP)))
    check('键集合完全对上', set(prog) == set(W.PROGRESSION_MAP),
          sorted(set(prog) ^ set(W.PROGRESSION_MAP)))
    mismatch = []
    for key, const in W.PROGRESSION_MAP.items():
        if int(getattr(m, const)) != int(prog[key]):
            mismatch.append((key, prog[key], getattr(m, const)))
    check('每个值都等于对应常量', not mismatch, mismatch)
    check('默认配置自身校验通过', W.validate(cfg) == [], W.validate(cfg))

    print('[2] 改配置真的改到模块常量')
    tmpdir = tempfile.mkdtemp(prefix='m4prog_')
    path = os.path.join(tmpdir, 'world_config.json')
    c2 = copy.deepcopy(cfg)
    c2['progression']['exp_per_enemy'] = 123
    c2['progression']['grade_max'] = 40
    c2['progression']['coin_init'] = 777
    W.save_config(c2, path)
    old = {k: getattr(m, v) for k, v in W.PROGRESSION_MAP.items()}
    try:
        applied, msg = W.apply_to(m, path)
        check('apply 成功', applied is True, msg)
        check('exp_per_enemy 生效', m.BATTLE_EXP_PER_ENEMY == 123,
              m.BATTLE_EXP_PER_ENEMY)
        check('grade_max 生效', m.GRADE_MAX == 40, m.GRADE_MAX)
        check('coin_init 生效', m.COIN_INIT == 777, m.COIN_INIT)
    finally:
        # 还原，别污染后续测试
        for key, const in W.PROGRESSION_MAP.items():
            setattr(m, const, old[key])

    print('[3] 旧配置（无 progression 段）兼容：不报错、保留默认')
    c3 = copy.deepcopy(cfg)
    del c3['progression']
    check('缺段也能过校验', W.validate(c3) == [], W.validate(c3))
    path3 = os.path.join(tmpdir, 'old.json')
    W.save_config(c3, path3)
    before = m.BATTLE_EXP_PER_ENEMY
    applied, msg = W.apply_to(m, path3)
    check('缺段仍能应用（地图不丢）', applied is True, msg)
    check('缺段时保留模块默认', m.BATTLE_EXP_PER_ENEMY == before,
          (before, m.BATTLE_EXP_PER_ENEMY))
    check('60 张地图仍在', len(m.MAPS) >= 60, len(m.MAPS))

    print('[4] 非法值必须报错')
    def errs_with(mutator):
        c = copy.deepcopy(cfg)
        mutator(c['progression'])
        return W.validate(c)

    check('越界 grade_max=999 报错',
          any('grade_max' in e for e in errs_with(
              lambda p: p.__setitem__('grade_max', 999))))
    check('非整数报错',
          any('exp_per_enemy' in e for e in errs_with(
              lambda p: p.__setitem__('exp_per_enemy', 'abc'))))
    check('未知字段报错',
          any('未知字段' in e for e in errs_with(
              lambda p: p.__setitem__('no_such_key', 1))))
    check('progression 不是对象报错',
          any('必须是对象' in e for e in
              (lambda c: (c.__setitem__('progression', []), W.validate(c))[1])(
                  copy.deepcopy(cfg))))

    print('[5] 落盘文件本身合法')
    real = W.load_config()
    check('仓库里的 world_config.json 能读', real is not None)
    if real:
        check('真实配置校验通过', W.validate(real) == [], W.validate(real))
        check('真实配置带 progression', isinstance(real.get('progression'), dict))
        check('真实配置仍是 60 张图',
              len(real.get('world', {}).get('maps', [])) >= 60,
              len(real.get('world', {}).get('maps', [])))

    print()
    if fails:
        print('FAIL（%d 项）' % len(fails))
        for f in fails:
            print('  ✗ %s' % f)
        return 1
    print('progression 配置契约 PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
