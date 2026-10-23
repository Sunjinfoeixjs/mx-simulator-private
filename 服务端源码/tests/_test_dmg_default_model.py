# -*- coding: utf-8 -*-
"""默认物伤模型守护：防止有人把默认模型改回 linear。

背景（2026-10-20）：项目结论「linear 是便利选择、来源不可考；reverse 才有反编译
出处」——默认模型因此翻转为 `reverse`。但这两个地方都能被悄悄改回 linear：

  · `m4_server.DMG_MODEL` 模块常量：删文件 / `--no-world-config` / 新机器未跑 prepare
    时，内核直接吃这个默认值；
  · `world_config.json` 的 `battle.dmg_model`：正常起服走的配置路径。

两处任一改回 linear，默认配置就不再有反编译出处——而伤害数字（50/48/20）两个
模型都能复现，**光看游戏根本发现不了**。所以这里把「默认 = reverse」钉死。

注意：本测试**不起内核、不调 world_config.apply_to**（避免任何覆盖干扰），只测
「默认应该是什么」。每个测试在独立子进程跑，import 即拿到真实代码默认值。
"""
from __future__ import print_function

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

import m4_server as m                       # noqa: E402

fails = []


def check(cond, msg):
    print(('OK   ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)


# ── 1. 代码默认常量：DMG_MODEL 必须是 reverse ──
#    这是「删配置 / --no-world-config / 配置校验失败」兜底时的取值。
check(m.DMG_MODEL == 'reverse',
      '代码默认 DMG_MODEL 必须是 reverse（当前=%r）；改回 linear 会让默认配置失去'
      '反编译出处，而 50/48/20 两个模型都能复现、游戏里发现不了' % m.DMG_MODEL)

# ── 2. 随附配置 world_config.json：battle.dmg_model 必须是 reverse ──
#    这是「正常起服」走的路径；文件存在且校验通过时无条件覆盖代码默认。
cfg_path = os.path.join(SIM, 'world_config.json')
ok = os.path.isfile(cfg_path)
check(ok, 'world_config.json 存在（%s）' % cfg_path)
if ok:
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    model = cfg.get('battle', {}).get('dmg_model')
    check(model == 'reverse',
          "world_config.json 的 battle.dmg_model 必须是 reverse（当前=%r）" % model)

# ── 3. default_config() 反推值也必须一致 ──
#    prepare_local_client 等工具用它生成配置；若源码常量已是 reverse，这里必然 reverse，
#    但显式钉一道，免得将来 default_config 改了取值来源而没同步常量。
import world_config as wc                         # noqa: E402
dc = wc.default_config(m)
check(dc.get('battle', {}).get('dmg_model') == 'reverse',
      'world_config.default_config() 反推的 dmg_model 必须是 reverse'
      '（当前=%r）' % dc.get('battle', {}).get('dmg_model'))

print('')
if fails:
    print('==== %d 条失败 ====' % len(fails))
    for f in fails:
        print('  x %s' % f)
    sys.exit(1)
print('==== 默认物伤模型守护通过（reverse）====')
sys.exit(0)
