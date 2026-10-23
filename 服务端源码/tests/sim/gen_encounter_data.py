# -*- coding: utf-8 -*-
"""生成 `encounter_data.py`：场景 -> 怪物 -> 客户端造型(shape) 的遇怪表。

真值来源（三条，均可复查）
  1. 场景 / 场景等级 / 怪物名单：《梦想世界2013资料全库》「暗雷/明雷场景」表
     —— `drop_data.DROP_BY_SCENE` 已是该表的解析产物（gen_drop_data.py 生成）。
  2. 怪物名 -> 造型 shape：客户端 `src/data/monsterdata.py` 的 `DATA`（105 条）。
     该文件是反编译产物，末尾有模块级裸 `return`，不能直接 import，这里按行改写后 exec。
  3. 场景名 -> mapid：`world_config.json` 的 `world.maps`（60 张，mapdata_resolve.py 联立所得）。

线上交叉核对（2026-10-22）
  · 叶子猪《梦想世界》端游资料站「召唤兽分布」：https://mx.yzz.cn/call/2010,100673.shtml
    长风岛/天水平原/香叶丛林/…/初云平原 共 40+ 场景的怪物名单与掉落物品与本表逐条一致。
  · 该页同时给出「场景等级只表示遇敌等级条件，不代表怪物等级」——所以本表**只**把
    level 当作场景等级用，不冒充怪物等级。

用法：
    python gen_encounter_data.py
产物：
    encounter_data.py       （勿手改）
    encounter_resolve.tsv   （逐场景的解析明细，未解析项留空并标 UNRESOLVED）
"""
from __future__ import print_function

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(HERE))          # …\MX
MONSTERDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'monsterdata.py')
WORLD_CFG = os.path.join(HERE, 'world_config.json')
OUT = os.path.join(HERE, 'encounter_data.py')
REPORT = os.path.join(HERE, 'encounter_resolve.tsv')

sys.path.insert(0, HERE)
import drop_data  # noqa: E402

# monsterdata 里 4 条名字是双重编码的乱码（GBK 文本被再按 latin-1 存回），
# 按 id 直接给真名，不猜、不按相似度匹配。
SHAPE_ALIAS = {
    2160: '小猫女',
    2250: '小魔女',
    2260: '小魔头',
    2390: '魔蝎',
}


def load_monsterdata():
    """返回 {名称: shape}。容忍反编译文件里的模块级裸 return。"""
    src = io.open(MONSTERDATA, encoding='utf-8', errors='ignore').read()
    fixed = []
    for ln in src.split('\n'):
        fixed.append('pass' if ln.strip() == 'return' else ln)
    ns = {}
    exec('\n'.join(fixed), ns)
    data = ns['DATA']
    name2shape = {}
    for mid, row in data.items():
        shape = int(row.get('shape') or mid)
        name = row.get('name')
        if isinstance(name, bytes):
            name = name.decode('gbk', 'replace')
        name = SHAPE_ALIAS.get(int(mid), name)
        if name and name not in name2shape:
            name2shape[name] = shape
    return name2shape


def normalize(name):
    """去掉场景表里的括注（如「闪电侠（稀有）」→「闪电侠」）。

    资料表的括注是**标注**，不是名字的一部分：monsterdata 里只有「闪电侠」。
    只在括注整体为「稀有」时才去掉，其余括注保持原样（避免误伤）。
    """
    for suf in (u'（稀有）', u'(稀有)', u'（稀有召唤兽）'):
        if name.endswith(suf):
            return name[:-len(suf)]
    return name


def load_maps():
    cfg = json.load(io.open(WORLD_CFG, encoding='utf-8'))
    out = {}
    for mp in cfg['world']['maps']:
        out[mp['name']] = int(mp['mapid'])
    return out


def main():
    name2shape = load_monsterdata()
    scene2map = load_maps()

    map_enc = {}
    report = ['scene\tlevel\tmapid\tmonster\tkind\tshape\tstatus']
    unresolved = []
    for scene, info in drop_data.DROP_BY_SCENE.items():
        mapid = scene2map.get(scene)
        if mapid is None:
            report.append('%s\t%s\t-\t-\t-\t-\tNO_MAPID' % (scene, info.get('level')))
            continue
        monsters = []
        for name in info.get('monsters', []):
            shape = name2shape.get(name) or name2shape.get(normalize(name))
            kind = 'summon' if shape and shape >= 4000 else 'monster'
            status = 'OK' if shape else 'UNRESOLVED'
            if not shape:
                unresolved.append((scene, name))
            report.append('%s\t%s\t%d\t%s\t%s\t%s\t%s'
                          % (scene, info.get('level'), mapid, name, kind,
                             shape if shape else '-', status))
            if shape:
                # 显示名用客户端真名（去掉「（稀有）」这类资料表括注）
                monsters.append([name if name in name2shape else normalize(name),
                                 int(shape)])
        map_enc[mapid] = {
            'scene': scene,
            'level': int(info.get('level') or 0),
            'monsters': monsters,
        }

    lines = []
    lines.append('# -*- coding: utf-8 -*-')
    lines.append('"""场景遇怪表（生成物，勿手改）。')
    lines.append('')
    lines.append('由 `gen_encounter_data.py` 生成。真值边界：')
    lines.append('  OK 场景名 / 场景等级 / 怪物名单 = 真值（《梦想世界2013资料全库》场景表，')
    lines.append('     经叶子猪端游资料站同表逐条交叉核对，见 gen_encounter_data.py 头）。')
    lines.append('  OK 怪物名 -> shape = 真值（客户端 src/data/monsterdata.py，105 条全表）。')
    lines.append('  OK 场景名 -> mapid = 真值（world_config.json 的 world.maps）。')
    lines.append('  XX **场景等级不是怪物等级**：资料原文「场景等级只表示遇敌的等级条件，')
    lines.append('     不代表怪物的等级」——所以 level 只用于「这张图该刷几级段的怪」这类判断。')
    lines.append('  XX 每场怪物数量 / 出现权重 / 怪物 HP = 未知（原服规则不可得），')
    lines.append('     本表只给「有哪些怪」，不给「几只、多大概率」。')
    lines.append('')
    lines.append('未解析怪物：%d 条%s' % (len(unresolved),
                                          ('（%s）' % unresolved) if unresolved else ''))
    lines.append('"""')
    lines.append('')
    lines.append('# 场景 -> (mapid, 场景等级, [[怪物名, shape], ...])')
    lines.append('SCENE_ENCOUNTERS = {')
    for scene, info in drop_data.DROP_BY_SCENE.items():
        mapid = scene2map.get(scene)
        mons = map_enc.get(mapid, {}).get('monsters', [])
        lines.append("    %r: (%r, %r, %r)," % (scene, mapid, info.get('level'), mons))
    lines.append('}')
    lines.append('')
    lines.append('# mapid -> 遇怪定义（仅野外/迷宫；城镇不在此表 = 不遇怪）')
    lines.append('MAP_ENCOUNTERS = {')
    for mapid in sorted(map_enc):
        e = map_enc[mapid]
        lines.append("    %d: {'scene': %r, 'level': %d, 'monsters': %r},"
                     % (mapid, e['scene'], e['level'], e['monsters']))
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('def get(mapid):')
    lines.append("    return MAP_ENCOUNTERS.get(int(mapid))")
    lines.append('')

    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
    io.open(REPORT, 'w', encoding='utf-8').write('\n'.join(report))

    print('scenes=%d  maps=%d  unresolved=%d'
          % (len(drop_data.DROP_BY_SCENE), len(map_enc), len(unresolved)))
    print('written: %s' % OUT)
    print('written: %s' % REPORT)
    return 1 if unresolved else 0


if __name__ == '__main__':
    sys.exit(main())
