# -*- coding: utf-8 -*-
"""把 _gen_quest_716 的产物（摆位表 + 条目源码）合并进 quest_data.py。

只做锚点插入，不整文件重写：合并后可用 git diff 逐块复核。
"""
from __future__ import print_function

import io
import os
import sys

MX = r'C:\Users\Administrator\MX'
QD = os.path.join(MX, '服务端源码', 'tests', 'sim', 'quest_data.py')

DOC_ADD = u'''
7–16（2026-10-22 新增，飞升前主线）的真值来源与边界
  OK 流程 + NPC 坐标 = 资料全库逐条「流程」+ 每条末尾的 NPC 坐标表（75 个摆位）。
  OK 等级门槛 / 剧情点 / 触发 NPC / 场景 = 一览表（quests_mainline.json，24 条）。
  OK 交叉校验：流程里散落的「剧情点 1 点」注解逐条相加 = 一览表 story_point
     （7–16 合计 27 点），144 个节点全部吻合 —— 两处独立表述互证。
  OK 坐标双源：世界 NPC（金大有/加洛国王/龙蛇老人…）取 npcposdata 真值，
     与资料坐标表有 3 处小差异（金大有 38,66 vs 28,62；薇薇安·赤瞳 40,31 vs 38,33；
     西夕·丛林秘语 64,57 vs 66,58），一律取 npcposdata（客户端世界数据）。
  !! 场景别名 2 处：资料「清风村」= 本库 mapid 1025（库内名「清风村2」，两者在客户端
     pathdata 的出口完全一致）；资料「珊瑚迷宫二层」= 本库单张「珊瑚迷宫」(1036)，
     客户端地图图无二层。
  XX 交付物本版缺失 2 处（刃虎宝宝 / 大螃蟹宝宝）：它们属「召唤兽」类，本版 itemdata
     连近似物都没有 —— 不给假 id、也不让流程卡死，该步降级为对话并在 step['note'] 写明。
  XX 奖励物品名与本版不一致 8 处，逐条记在 reward['items'][i]['note']
     （N级宝石包→N级如意宝石包、40/80 级召唤兽装备、N级大点金石、40–50 级招式启蒙、
     60 级打造装备、80 级制造指南书、空白经验心得）。
  XX 组队战斗在本服按 1 只敌方模拟（与 1–6 同口径）。
  17–22 未落地（见 NOT_IMPLEMENTED）：资料只给流程串、不给坐标表，且这些 NPC 在
     客户端 npcposdata / monsterdata 里一处都没有 —— 按「不猜坐标」整条挂起。
'''

QID_ADD = u'''Q_07 = 107      # 并雄的四国（2026-10-22 新增，飞升前主线 7–16）
Q_08 = 108      # 兔族的来客
Q_09 = 109      # 北方的狼族
Q_10 = 110      # 忘却的遗迹
Q_11 = 111      # 未知的力量
Q_12 = 112      # 天空的彼端
Q_13 = 113      # 远方的呼啸
Q_14 = 114      # 命运的脚步
Q_15 = 115      # 寂静的黑暗（前置：剧情 10/11/12/13/14）
Q_16 = 116      # 太阳神的启示
'''

ITEM_ADD = u'''    # 剧情 7–16（2026-10-22）
    '丝绸手绢': (25212, '丝绸手绢'),
    '神隐护腕': (63040, '神隐护腕'),
    '大力丸': (21108, '大力丸'),
    '荧光草': (25008, '荧光草'),
    '火神花': (21213, '火神花'),
    '七星戒指': (65040, '七星戒指'),
    '京剧脸谱': (25210, '京剧脸谱'),
    '紫砂茶壶': (25220, '紫砂茶壶'),
    '回命散': (21110, '回命散'),
    '驱邪丸': (21111, '驱邪丸'),
    '甘草': (21233, '甘草'),
    '仙灵石': (26010, '仙灵石'),
'''

NOT_IMPL = u'''# 一览表里有、但本服**未落地**的条目（no → 缺口）。
# 不参与引擎，只登记编号/名称/触发 NPC/场景/等级/剧情点/节点数 + 卡点，
# 做到「可查、不假装已做」。
# 17–22 的共同结论：资料只给「流程串」（谁→谁→谁），**没给这些 NPC 的坐标表**；
# 客户端 npcposdata（世界 NPC 表）与 monsterdata（105 个造型）里这些名字一处都没有
# —— 场景与坐标双双不可得。按「不猜坐标、不造数据」整条挂起，不留半截流程。
NOT_IMPLEMENTED = {
    17: {'name': '沙漠的尘埃', 'npc': '烈阳锋·血牙', 'map': '夜幕镇',
         'level': 90, 'point': 1, 'flow_steps': 30,
         'blocked': '枢纽 NPC「老狼」在 30 个节点里出现 14 次，无任何坐标来源',
         'missing_npcs': ('老狼', '沙之守护·蛇', '沙之守护·鬼', '沙之守护·神',
                          '烈火凤凰', '狼尸王')},
    18: {'name': '冰封的大地', 'npc': '薇薇安·赤瞳', 'map': '黎明镇',
         'level': 100, 'point': 2, 'flow_steps': 19,
         'blocked': '枢纽 NPC「大地之树」串联 6 个节点，无任何坐标来源',
         'missing_npcs': ('初·魔树', '大地之树', '守望者', '极·魔树',
                          '矮树灵', '冰之青莲', '白之妖妖')},
    19: {'name': '天界之路', 'npc': '天乾', 'map': '夜幕镇',
         'level': 90, 'point': 0, 'flow_steps': 8,
         'blocked': '三场战斗的对象（三杀兽·牙/角/鳞）无坐标，无法摆位',
         'missing_npcs': ('三杀兽·牙', '三杀兽·角', '三杀兽·鳞')},
    20: {'name': '神秘之地', 'npc': '蒙面怪客', 'map': '天空要塞',
         'level': 110, 'point': 2, 'flow_steps': 20,
         'blocked': '场景「天空要塞」本服无该 mapid（npcposdata 记 1157，本库只有 '
                    '1158/1159/1176/1177）；大水晶/小水晶等 7 个 NPC 亦无坐标',
         'missing_npcs': ('大水晶', '小水晶', '时空异兽·紫', '时空异兽·白',
                          '时空异兽·金', '时空异兽·青', '虚空幻魔')},
    21: {'name': '魔界之门', 'npc': '神羿', 'map': '初云平原',
         'level': 105, 'point': 0, 'flow_steps': 10,
         'blocked': '四场战斗的对象（圣灵·冰龙/炎虎/雪凰/玄龟）无坐标，无法摆位',
         'missing_npcs': ('圣灵·冰龙', '圣灵·炎虎', '圣灵·雪凰', '圣灵·玄龟')},
    22: {'name': '低沉的乌云', 'npc': '天乾', 'map': '夜幕镇',
         'level': 120, 'point': 0, 'flow_steps': 14,
         'blocked': '沐斯·栖风/夜痕/冷云 三个战斗对象无坐标；资料流程串里也无收尾地点',
         'missing_npcs': ('沐斯·栖风', '夜痕', '冷云')},
}
'''


def read():
    return io.open(QD, encoding='utf-8').read()


def write(s):
    io.open(QD, 'w', encoding='utf-8', newline='\n').write(s)


def main():
    s = read()
    spawns = io.open(os.path.join(MX, '_quest_716_spawns.txt'),
                     encoding='utf-8').read().rstrip('\n')
    quests = io.open(os.path.join(MX, '_quest_716_quests.txt'),
                     encoding='utf-8').read().rstrip('\n')
    n0 = len(s)

    # ① docstring：接在「客户端物品名与资料不一致的 4 处」段之后
    anchor = u"  · 幸运转转符 / 空白经验心得 / 40级召唤兽装备 → 本版查无此物，奖励记 None（不编 id）\n"
    assert s.count(anchor) == 1, 'docstring 锚点不唯一'
    s = s.replace(anchor, anchor + DOC_ADD)

    # ② qid 常量
    anchor = u"Q_JADE = 106         # 勇者的宝玉（2026-10-22 新增）\n"
    assert s.count(anchor) == 1, 'qid 锚点不唯一'
    s = s.replace(anchor, anchor + u"\n" + QID_ADD)

    # ③ 剧情物品
    anchor = u"    '草编花环': (25203, '草编花环'),\n}"
    assert s.count(anchor) == 1, 'ITEM 锚点不唯一'
    s = s.replace(anchor, u"    '草编花环': (25203, '草编花环'),\n" + ITEM_ADD + u"}")

    # ④ NPC 摆位：插在 NPC_SPAWNS 的收尾括号前
    anchor = u"    (1017, 8142, 2090, 224, 10, '地头蛇', 'quest-spawn shape=SIM(蛇)'),\n)"
    assert s.count(anchor) == 1, 'NPC_SPAWNS 锚点不唯一'
    s = s.replace(anchor, anchor[:-1] + u"\n" + spawns + u"\n)")

    # ⑤ QUESTS 条目：插在字典收尾前
    anchor = u"\n}\n\n# 资料一览表里 1–6 没有跨任务前置；"
    assert s.count(anchor) == 1, 'QUESTS 锚点不唯一'
    s = s.replace(anchor, u"\n\n" + quests + anchor)

    # ⑥ LATER_PREV 注释：15 已落地，改掉「等实现到那几条」的过期说法
    old = u"# /「完成以上所有」）。这里把后者也记下来，等实现到那几条时直接按数据校验。"
    assert s.count(old) == 1, 'LATER_PREV 注释锚点不唯一'
    s = s.replace(old, u"# /「完成以上所有」）。15 已落地（prev 由本表 no→qid 换算，见 "
                       u"_gen_quest_716）；\n# 17/18/22 待落地，届时同样按本表换算。")

    # ⑦ NOT_IMPLEMENTED 整块替换
    beg = s.index(u"# 一览表里有、但本服还没实现的条目")
    end = s.index(u"QUEST_IDS = tuple(sorted(QUESTS))")
    s = s[:beg] + NOT_IMPL + u"\n" + s[end:]

    write(s)
    print('quest_data.py: %d → %d 字节' % (n0, len(s)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
