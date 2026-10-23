# -*- coding: utf-8 -*-
"""
真值战斗公式模块
来源：客户端 petfightdata.py、petdata.py、_docx_text.txt 资料库
分级标注：
  ✅ 真值：客户端代码/数据表直接给出的公式与常数
  ⚠️ 半真值：资料库描述定性规则，数值需实测
  🔴 模拟：原服无对应数据，本服自定义
"""

from __future__ import print_function

# ───────────────────────────── 宠物战斗属性真值公式 (petfightdata.py) ─────────────────────────────
# ✅ 真值：客户端 petfightdata.py GetPetData() 直接给出的成长公式
# lv = 该资质等级 (petinfo[3..8] 对应 hp/at/df/mg/md/sp)
PET_FORMULAS = {
    'hp':  lambda lv: lv*lv*5 + lv*100,
    'at':  lambda lv: lv**1.5 * 1.5 + lv*40,
    'df':  lambda lv: lv**1.5 * 1.4 + lv*30,
    'mg':  lambda lv: lv**1.5 * 1.5 + lv*40,  # 法攻/灵力
    'md':  lambda lv: lv**1.5 * 1.4 + lv*30,  # 法防
    'sp':  lambda lv: (1.13**(lv-1)) * 30,    # 速度
}

# 资质索引映射 (petfightdata.py 第 11-12 行)
# attr = [3, 4, 5, 6, 7, 8] 对应 hp, at, df, mg, md, sp
PET_ATTR_IDX = {
    3: 'hp',   # 体质/气血资质
    4: 'at',   # 攻击资质
    5: 'df',   # 防御资质
    6: 'mg',   # 法力/灵力资质
    7: 'md',   # 法防资质
    8: 'sp',   # 速度资质
}

# ───────────────────────────── 种族修正系数 FactorData (petfightdata.py) ─────────────────────────────
# ✅ 真值：客户端完整表，每个 pet idx 对应一组系数
# 字段：hp, at, df, mg, md, sp 为乘法系数；lv 为初始资质等级偏移
FACTOR_DATA = {
    51:  {'name': '野猪',           'lv': 1, 'hp': 1.0, 'at': 2.4, 'df': 0.8, 'mg': 1.2, 'md': 0.8, 'sp': 1.8},
    52:  {'name': '野猪王',         'lv': 1, 'hp': 1.0, 'at': 2.4, 'df': 1.5, 'mg': 1.2, 'md': 1.0, 'sp': 0.9},
    53:  {'name': '赤狐',           'lv': 1, 'hp': 1.3, 'at': 2.4, 'df': 1.0, 'mg': 1.1, 'md': 1.0, 'sp': 1.2},
    54:  {'name': '狸猫',           'lv': 1, 'hp': 1.2, 'at': 2.4, 'df': 0.8, 'mg': 1.3, 'md': 0.9, 'sp': 1.4},
    55:  {'name': '树精',           'lv': 2, 'hp': 0.8, 'at': 2.4, 'df': 0.8, 'mg': 1.6, 'md': 0.8, 'sp': 1.2},
    56:  {'name': '野猪精',         'lv': 2, 'hp': 1.1, 'at': 2.4, 'df': 1.0, 'mg': 0.9, 'md': 1.0, 'sp': 1.2},
    57:  {'name': '蝴蝶仙子',       'lv': 2, 'hp': 1.1, 'at': 0.9, 'df': 0.8, 'mg': 2.4, 'md': 1.2, 'sp': 1.2},
    58:  {'name': '曼陀罗',         'lv': 2, 'hp': 1.3, 'at': 2.4, 'df': 1.0, 'mg': 0.9, 'md': 0.8, 'sp': 1.2},
    71:  {'name': '菊花仙',         'lv': 1, 'hp': 1.0, 'at': 1.2, 'df': 1.0, 'mg': 2.4, 'md': 1.2, 'sp': 1.2},
    72:  {'name': '野猪大王',       'lv': 1, 'hp': 1.3, 'at': 1.2, 'df': 1.2, 'mg': 2.4, 'md': 1.0, 'sp': 0.9},
    73:  {'name': '蜘蛛精',         'lv': 1, 'hp': 1.3, 'at': 1.2, 'df': 1.1, 'mg': 2.4, 'md': 1.1, 'sp': 0.9},
    74:  {'name': '百花仙',         'lv': 1, 'hp': 1.1, 'at': 2.4, 'df': 1.2, 'mg': 1.3, 'md': 0.8, 'sp': 1.2},
    77:  {'name': '幽灵狼',         'lv': 2, 'hp': 0.6, 'at': 2.4, 'df': 1.0, 'mg': 1.8, 'md': 1.0, 'sp': 0.8},
    78:  {'name': '百花仙子',       'lv': 2, 'hp': 1.0, 'at': 1.0, 'df': 1.0, 'mg': 2.4, 'md': 1.0, 'sp': 1.2},
    79:  {'name': '蛤蟆精',         'lv': 2, 'hp': 1.2, 'at': 1.2, 'df': 1.0, 'mg': 2.4, 'md': 1.0, 'sp': 0.8},
    80:  {'name': '狐狸精',         'lv': 2, 'hp': 0.8, 'at': 1.0, 'df': 1.0, 'mg': 2.4, 'md': 1.2, 'sp': 1.2},
    75:  {'name': '牛妖',           'lv': 2, 'hp': 1.0, 'at': 1.0, 'df': 1.0, 'mg': 2.4, 'md': 1.0, 'sp': 1.2},
    76:  {'name': '白骨精',         'lv': 2, 'hp': 0.6, 'at': 1.2, 'df': 1.0, 'mg': 2.4, 'md': 1.0, 'sp': 1.4},
    59:  {'name': '蝴蝶仙',         'lv': 3, 'hp': 0.9, 'at': 2.4, 'df': 0.9, 'mg': 1.2, 'md': 0.7, 'sp': 1.2},
    60:  {'name': '狐狸仙',         'lv': 3, 'hp': 0.9, 'at': 1.2, 'df': 1.0, 'mg': 1.4, 'md': 0.9, 'sp': 1.2},
    61:  {'name': '白骨仙',         'lv': 3, 'hp': 0.7, 'at': 1.7, 'df': 0.7, 'mg': 2.4, 'md': 0.8, 'sp': 1.0},
    81:  {'name': '千年蛤蟆',       'lv': 3, 'hp': 1.0, 'at': 1.0, 'df': 1.0, 'mg': 2.4, 'md': 1.0, 'sp': 0.9},
    82:  {'name': '蝴蝶仙子',       'lv': 3, 'hp': 0.9, 'at': 1.1, 'df': 1.0, 'mg': 1.6, 'md': 0.9, 'sp': 1.1},
    83:  {'name': '牛魔王',         'lv': 3, 'hp': 0.7, 'at': 1.3, 'df': 0.7, 'mg': 2.4, 'md': 0.7, 'sp': 1.4},
    62:  {'name': '太岁',           'lv': 4, 'hp': 0.7, 'at': 2.4, 'df': 1.0, 'mg': 0.9, 'md': 0.8, 'sp': 1.2},
    63:  {'name': '狐狸仙子',       'lv': 4, 'hp': 0.7, 'at': 1.4, 'df': 0.6, 'mg': 1.4, 'md': 0.7, 'sp': 1.7},
    64:  {'name': '蝴蝶仙女',       'lv': 4, 'hp': 1.0, 'at': 1.3, 'df': 0.9, 'mg': 1.3, 'md': 1.0, 'sp': 1.0},
    84:  {'name': '千年太岁',       'lv': 4, 'hp': 0.8, 'at': 1.0, 'df': 1.1, 'mg': 2.4, 'md': 0.8, 'sp': 0.9},
    85:  {'name': '白骨夫人',       'lv': 4, 'hp': 0.8, 'at': 1.0, 'df': 1.0, 'mg': 1.6, 'md': 1.1, 'sp': 1.0},
    86:  {'name': '九尾狐',         'lv': 4, 'hp': 0.9, 'at': 1.2, 'df': 1.2, 'mg': 1.2, 'md': 1.1, 'sp': 0.9},
}

# ───────────────────────────── 基础修正值 CorrectData (petfightdata.py) ─────────────────────────────
# ✅ 真值：各种族基础数值修正 (加在公式结果上)
CORRECT_DATA = {
    51:  {'name': '野猪',           'lv': 1, 'hp': 200, 'at': 480, 'df': 160, 'mg': 240, 'md': 160, 'sp': 360},
    52:  {'name': '野猪王',         'lv': 1, 'hp': 200, 'at': 480, 'df': 300, 'mg': 240, 'md': 200, 'sp': 180},
    53:  {'name': '赤狐',           'lv': 1, 'hp': 260, 'at': 480, 'df': 200, 'mg': 220, 'md': 200, 'sp': 240},
    54:  {'name': '狸猫',           'lv': 1, 'hp': 240, 'at': 480, 'df': 160, 'mg': 260, 'md': 180, 'sp': 280},
    55:  {'name': '树精',           'lv': 2, 'hp': 160, 'at': 480, 'df': 160, 'mg': 320, 'md': 160, 'sp': 240},
    56:  {'name': '野猪精',         'lv': 2, 'hp': 220, 'at': 480, 'df': 200, 'mg': 180, 'md': 200, 'sp': 240},
    57:  {'name': '蝴蝶仙子',       'lv': 2, 'hp': 220, 'at': 180, 'df': 160, 'mg': 480, 'md': 240, 'sp': 240},
    58:  {'name': '曼陀罗',         'lv': 2, 'hp': 260, 'at': 480, 'df': 200, 'mg': 180, 'md': 160, 'sp': 240},
    71:  {'name': '菊花仙',         'lv': 1, 'hp': 200, 'at': 240, 'df': 200, 'mg': 480, 'md': 240, 'sp': 240},
    72:  {'name': '野猪大王',       'lv': 1, 'hp': 260, 'at': 240, 'df': 240, 'mg': 480, 'md': 200, 'sp': 180},
    73:  {'name': '蜘蛛精',         'lv': 1, 'hp': 260, 'at': 240, 'df': 220, 'mg': 480, 'md': 220, 'sp': 180},
    74:  {'name': '百花仙',         'lv': 1, 'hp': 220, 'at': 480, 'df': 240, 'mg': 260, 'md': 160, 'sp': 240},
    77:  {'name': '幽灵狼',         'lv': 2, 'hp': 120, 'at': 480, 'df': 200, 'mg': 360, 'md': 200, 'sp': 160},
    78:  {'name': '百花仙子',       'lv': 2, 'hp': 200, 'at': 200, 'df': 200, 'mg': 480, 'md': 200, 'sp': 240},
    79:  {'name': '蛤蟆精',         'lv': 2, 'hp': 240, 'at': 240, 'df': 200, 'mg': 480, 'md': 200, 'sp': 160},
    80:  {'name': '狐狸精',         'lv': 2, 'hp': 160, 'at': 200, 'df': 200, 'mg': 480, 'md': 240, 'sp': 240},
    75:  {'name': '牛妖',           'lv': 2, 'hp': 200, 'at': 200, 'df': 200, 'mg': 480, 'md': 200, 'sp': 240},
    76:  {'name': '白骨精',         'lv': 2, 'hp': 120, 'at': 240, 'df': 200, 'mg': 480, 'md': 200, 'sp': 280},
    59:  {'name': '蝴蝶仙',         'lv': 3, 'hp': 180, 'at': 480, 'df': 180, 'mg': 240, 'md': 140, 'sp': 240},
    60:  {'name': '狐狸仙',         'lv': 3, 'hp': 180, 'at': 240, 'df': 200, 'mg': 280, 'md': 180, 'sp': 240},
    61:  {'name': '白骨仙',         'lv': 3, 'hp': 140, 'at': 340, 'df': 140, 'mg': 480, 'md': 160, 'sp': 200},
    81:  {'name': '千年蛤蟆',       'lv': 3, 'hp': 200, 'at': 200, 'df': 200, 'mg': 480, 'md': 200, 'sp': 180},
    82:  {'name': '蝴蝶仙子',       'lv': 3, 'hp': 180, 'at': 220, 'df': 200, 'mg': 320, 'md': 180, 'sp': 220},
    83:  {'name': '牛魔王',         'lv': 3, 'hp': 140, 'at': 260, 'df': 140, 'mg': 480, 'md': 140, 'sp': 280},
    62:  {'name': '太岁',           'lv': 4, 'hp': 140, 'at': 480, 'df': 200, 'mg': 180, 'md': 160, 'sp': 240},
    63:  {'name': '狐狸仙子',       'lv': 4, 'hp': 140, 'at': 280, 'df': 120, 'mg': 280, 'md': 140, 'sp': 340},
    64:  {'name': '蝴蝶仙女',       'lv': 4, 'hp': 200, 'at': 260, 'df': 180, 'mg': 260, 'md': 200, 'sp': 200},
    84:  {'name': '千年太岁',       'lv': 4, 'hp': 160, 'at': 200, 'df': 220, 'mg': 480, 'md': 160, 'sp': 180},
    85:  {'name': '白骨夫人',       'lv': 4, 'hp': 160, 'at': 200, 'df': 200, 'mg': 320, 'md': 220, 'sp': 200},
    86:  {'name': '九尾狐',         'lv': 4, 'hp': 180, 'at': 240, 'df': 240, 'mg': 240, 'md': 220, 'sp': 180},
}

# ───────────────────────────── 宠物基础信息 (petdata.py) ─────────────────────────────
# ✅ 真值：shape → {name, liupai, attack_type, carry_grade}
# 关键条目：
PET_BASE_INFO = {
    1:    {'name': '梦想企鹅',    'liupai': '暗器', 'attack_type': '力敌', 'carry_grade': 10},   # 4005 在 petdata 里是 4005，但 FactorData 里是 51? 需对照
    2130: {'name': '野狼',        'liupai': '儒家', 'attack_type': '力敌', 'carry_grade': 15},
    4005: {'name': '梦想企鹅',    'liupai': '暗器', 'attack_type': '力敌', 'carry_grade': 10},
    # ... 其他 shape 可按需补全
}

# ───────────────────────────── 宠物技能表 (petfightdata.py SkillData) ─────────────────────────────
# ✅ 真值：完整技能列表
PET_SKILL_DATA = {
    1001: {'name': '泰山压顶', 'type': '物理', 'desc': '单体物理攻击，威力较大'},
    1002: {'name': '横扫',     'type': '物理', 'desc': '群体物理攻击'},
    1003: {'name': '震慑',     'type': '物理', 'desc': '降低目标速度'},
    1004: {'name': '连击',     'type': '物理', 'desc': '连续攻击2次'},
    1005: {'name': '反震',     'type': '物理', 'desc': '受到物理攻击时反击'},
    1006: {'name': '吸力',     'type': '物理', 'desc': '吸取目标力道恢复自身气血'},
    1007: {'name': '吸魔',     'type': '物理', 'desc': '吸取目标魔法恢复自身魔法'},
    1008: {'name': '鬼魂',     'type': '物理', 'desc': '免疫物理攻击'},
    1009: {'name': '高级连击', 'type': '物理', 'desc': '连续攻击，威力递增'},
    1010: {'name': '高级吸力', 'type': '物理', 'desc': '吸取力道并造成伤害'},
    1051: {'name': '烈火',     'type': '法术', 'desc': '单体火系法术'},
    1052: {'name': '水攻',     'type': '法术', 'desc': '单体水系法术'},
    1053: {'name': '落雷',     'type': '法术', 'desc': '单体雷系法术'},
    1054: {'name': '飞砂走石', 'type': '法术', 'desc': '单体土系法术'},
    1055: {'name': '三昧真火', 'type': '法术', 'desc': '单体火系强力法术'},
    1056: {'name': '水漫金山', 'type': '法术', 'desc': '单体水系强力法术'},
    1057: {'name': '五雷轰顶', 'type': '法术', 'desc': '单体雷系强力法术'},
    1058: {'name': '无中生有', 'type': '法术', 'desc': '单体土系强力法术'},
    1059: {'name': '地狱烈火', 'type': '法术', 'desc': '群体火系法术'},
    1060: {'name': '水淹七军', 'type': '法术', 'desc': '群体水系法术'},
    1061: {'name': '万雷天牢', 'type': '法术', 'desc': '群体雷系法术'},
    1062: {'name': '地动山摇', 'type': '法术', 'desc': '群体土系法术'},
    1063: {'name': '业火焚身', 'type': '法术', 'desc': '群体火系强力法术'},
    1064: {'name': '水火既济', 'type': '法术', 'desc': '群体水系强力法术'},
    1065: {'name': '雷动九天', 'type': '法术', 'desc': '群体雷系强力法术'},
    1066: {'name': '地裂天崩', 'type': '法术', 'desc': '群体土系强力法术'},
    1067: {'name': '宁心',     'type': '辅助', 'desc': '解除封印状态'},
    1068: {'name': '复活',     'type': '辅助', 'desc': '复活倒地队友'},
}

# ───────────────────────────── 五行/抗性系统 (资料库) ─────────────────────────────
# ✅ 真值：资料库第 9118-9121 行
RESIST_TYPES = ['力敌', '灵巧', '群攻', '普通']  # 正%抵抗，负%削弱

# ───────────────────────────── 控制点系统 (资料库第 9128-9134 行) ─────────────────────────────
# ⚠️ 半真值：资料库描述机制，初始值/恢复速度需实测
CONTROL_POINTS = {
    'npc_battle_init': {'atk': 30, 'def': 30, 'dis': 30},  # 对NPC战斗初始各30
    'pvp_battle_init': {'atk': 0,  'def': 0,  'dis': 0},    # 对玩家战斗初始各0
    # 恢复机制：资料库提到"装备特效"影响恢复，具体数值未给出
}

# ───────────────────────────── 逃跑机制 (资料库第 30998-31030 行) ─────────────────────────────
# ⚠️ 半真值：资料库定性描述
# "逃跑并非百分百成功，有一定几率失败，失败的话则会被对方白打一回合"
# "担任队长时，可在战斗中降低对方逃跑成功率；技能等级越高，效果越好"
# "提高自身在战斗中的逃跑成功率；技能等级越高，效果越好"
# "与普通NPC战斗时，逃跑成功几率大幅度增加"
ESCAPE_MECHANICS = {
    'base_success_rate': 0.5,      # 基础成功率 (需实测)
    'npc_bonus': 0.3,              # 对NPC战斗加成
    'leader_reduce_enemy': 0.1,    # 队长降低对方逃跑率
    'self_increase': 0.1,          # 自身提高逃跑率
    'fail_penalty': 'free_hit',    # 失败惩罚：对方白打一回合
}

# ───────────────────────────── 愤怒/特技系统 (资料库第 30917 行) ─────────────────────────────
# ⚠️ 半真值：初始值文档给定，增长机制需实测
RAGE_SYSTEM = {
    'solo_pvp_init': 80,           # 单人PvP初始80
    'team_pvp_init': 40,           # 组队PvP/对NPC初始40
    'max': 150,                    # 上限150
    # "受到伤害增加愤怒，伤害越大增加越多" - 具体公式需实测
}

# ───────────────────────────── 怪物等级影响 (资料库第 21083 行) ─────────────────────────────
# ⚠️ 半真值："普通怪物：攻击和防御能力受到等级影响"
# 具体公式：客户端无，服务端算法需反推或实测
MONSTER_LEVEL_SCALING = {
    'atk_per_level': 1.0,    # 每级攻击增长 (模拟)
    'def_per_level': 0.8,    # 每级防御增长 (模拟)
    'hp_per_level': 5.0,     # 每级血量增长 (模拟)
}

# ───────────────────────────── 核心计算函数 ─────────────────────────────

def calc_pet_stats(pet_idx, pet_lv, attr_lv_dict, tire=0):
    """
    真值计算宠物战斗属性
    :param pet_idx: FactorData/CorrectData 中的 idx (如 51=野猪, 63=狐狸仙子)
    :param pet_lv: 宠物等级
    :param attr_lv_dict: {3:hp_lv, 4:at_lv, 5:df_lv, 6:mg_lv, 7:md_lv, 8:sp_lv} 资质等级
    :param tire: 当前疲劳值 (0=tire_max 满忠诚)
    :return: {hp, at, df, mg, md, sp, tire_rate}
    """
    factor = FACTOR_DATA.get(pet_idx, FACTOR_DATA[51])
    correct = CORRECT_DATA.get(pet_idx, CORRECT_DATA[51])
    
    # 基础成长
    base = {}
    for attr_idx, attr_name in PET_ATTR_IDX.items():
        lv = attr_lv_dict.get(attr_idx, factor.get(attr_name, 1))  # 默认用种族基础等级
        formula = PET_FORMULAS[attr_name]
        base[attr_name] = int(formula(lv) * factor[attr_name] + correct[attr_name])
    
    # 疲劳度修正 (petfightdata.py 第 28-35 行)
    attr_total = sum(attr_lv_dict.get(i, 1) for i in range(3, 9))
    tire_max = 190 + attr_total * 2
    if tire <= 0:
        rate = 1.0
    else:
        rate = float(tire * 100 / tire_max) / 100
        if rate <= 0.5:
            rate = 0.8 - (0.5 - rate)
        else:
            rate = 1.0
    
    for k in base:
        base[k] = int(base[k] * rate)
    
    base['tire_rate'] = int(float(tire) / tire_max * 100) if tire_max > 0 else 100
    base['tire_max'] = tire_max
    return base


def calc_pet_zz(pet_idx):
    """真值计算宠物资质评价 (ZZ) - petfightdata.py GetPetZZ()"""
    factor = FACTOR_DATA.get(pet_idx, FACTOR_DATA[51])
    zz = {}
    for v in [1, 2, 3, 4, 5, 6]:  # 对应 hp, at, df, mg, md, sp
        zz[v] = int((3 - factor.get(PET_ATTR_IDX.get(v+2, 'hp'), 1)) * 1000)
    return zz


def calc_monster_stats(monster_lv, base_atk=10, base_def=5, base_hp=50):
    """半真值：怪物等级缩放 (资料库仅定性描述，具体系数模拟)"""
    return {
        'hp': int(base_hp + monster_lv * MONSTER_LEVEL_SCALING['hp_per_level']),
        'atk': int(base_atk + monster_lv * MONSTER_LEVEL_SCALING['atk_per_level']),
        'def': int(base_def + monster_lv * MONSTER_LEVEL_SCALING['def_per_level']),
    }


# ───────────────────────────── 测试/演示 ─────────────────────────────
if __name__ == '__main__':
    # 例：4005 梦想企鹅 (idx=51 野猪系)，1级，资质全1级
    print('=== 4005 梦想企鹅 1级 资质全1 ===')
    stats = calc_pet_stats(51, 1, {3:1,4:1,5:1,6:1,7:1,8:1}, tire=0)
    for k,v in stats.items():
        print(f'  {k}: {v}')
    
    print('\n=== 4005 梦想企鹅 10级 资质全10 ===')
    stats = calc_pet_stats(51, 10, {3:10,4:10,5:10,6:10,7:10,8:10}, tire=0)
    for k,v in stats.items():
        print(f'  {k}: {v}')
    
    print('\n=== 2130 野狼 (idx=63 狐狸仙子) 1级 ===')
    stats = calc_pet_stats(63, 1, {3:1,4:1,5:1,6:1,7:1,8:1}, tire=0)
    for k,v in stats.items():
        print(f'  {k}: {v}')
    
    print('\n=== 资质评价 ZZ (4005 idx=51) ===')
    print(calc_pet_zz(51))
    
    print('\n=== 资质评价 ZZ (2130 idx=63) ===')
    print(calc_pet_zz(63))