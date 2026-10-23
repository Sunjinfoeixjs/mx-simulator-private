# -*- coding: utf-8 -*-
"""把主线 1–3 的 NPC 摆位与剧情物品上架写进 world_config.json（幂等）。

为什么用脚本而不是手改 JSON：
  · 21 个 NPC 要分散插到 10 张图的 npcs 数组里，手改容易错位；
  · 脚本按 (mapid, npcid) 去重，重复跑不会增生；
  · 只 append / 覆盖同 id 条目，不动其它 NPC、不动地图顺序。

产物校验：跑完打印每张图新增/已存在的条目数。
"""
from __future__ import print_function

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, 'world_config.json')
sys.path.insert(0, HERE)
import quest_data as Q  # noqa: E402

# 剧情必需物品上架（价格是模拟值；itemdata 无价格字段）
SHOP_ADD = (
    (21610, '炸薯条', 50, 20),
    (25007, '辟邪草', 20, 50),
    (21411, '黑椒牛柳', 60, 20),
    (21100, '金疮药', 40, 50),
    (50030, '青霜剑', 3000, 5),      # 30 级武器，任务「王大雄」用
    # ── 主线 7–16 的交付物（2026-10-22）：不在商店就没人买得到，流程会断 ──
    (25212, '丝绸手绢', 80, 20),      # 7 并雄的四国
    (63040, '神隐护腕', 1200, 5),     # 8 兔族的来客（装备，价高）
    (21108, '大力丸', 60, 20),        # 8
    (25008, '荧光草', 20, 50),        # 8（要 20 棵，备货足）
    (21213, '火神花', 25, 50),        # 9 北方的狼族
    (65040, '七星戒指', 1500, 5),     # 9（装备）
    (25210, '京剧脸谱', 100, 20),     # 10 忘却的遗迹
    (25220, '紫砂茶壶', 100, 20),     # 10
    (21110, '回命散', 80, 20),        # 11 未知的力量
    (21111, '驱邪丸', 80, 20),        # 11
    (21233, '甘草', 20, 50),          # 13 远方的呼啸
    (26010, '仙灵石', 500, 10),       # 16 太阳神的启示
)


def main():
    cfg = json.load(io.open(CFG, encoding='utf-8'))
    maps = {m['mapid']: m for m in cfg['world']['maps']}

    added = replaced = 0
    for (mapid, npcid, shape, x, y, name, src) in Q.NPC_SPAWNS:
        mp = maps.get(mapid)
        if mp is None:
            print('!! mapid %d 不存在（NPC %s）' % (mapid, name))
            return 1
        entry = [npcid, shape, x, y, name]
        idx = next((i for i, n in enumerate(mp['npcs']) if n[0] == npcid), None)
        if idx is None:
            mp['npcs'].append(entry)
            added += 1
        else:
            if mp['npcs'][idx] != entry:
                mp['npcs'][idx] = entry
                replaced += 1

    shop = cfg['shop']['items']
    have = {int(it[0]) for it in shop}
    shop_added = 0
    for (shape, name, price, qty) in SHOP_ADD:
        if shape not in have:
            shop.append([shape, name, price, qty])
            shop_added += 1

    out = json.dumps(cfg, ensure_ascii=False, indent=1)
    io.open(CFG, 'w', encoding='utf-8', newline='\n').write(out)

    print('NPC：新增 %d / 覆盖 %d（共 %d 条摆位）'
          % (added, replaced, len(Q.NPC_SPAWNS)))
    print('商店：新增 %d 条（共 %d 条）' % (shop_added, len(shop)))
    for mid in sorted({s[0] for s in Q.NPC_SPAWNS}):
        print('  %-6d %-8s npc=%d' % (mid, maps[mid]['name'], len(maps[mid]['npcs'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
