# -*- coding: utf-8 -*-
"""世界数据配置层 —— 供可视化编辑器（editor_server.py）读写，m4_server.py 启动时据此覆盖默认值。

设计目标：
  · **不改代码即可改世界**：地图 / NPC / 出口 / 连通 / 战斗调参统一落在 world_config.json；
  · **零风险降级**：文件缺失或校验不通过时 m4_server 保持内置默认值，永不影响启动；
  · **单一事实来源**：`default_config()` 从 m4_server 当前默认值反推，
    保证首次生成的配置与现状**逐字段等价**（因此回归测试不受影响）。

配置结构（version 1）::

    {
      "version": 1, "updated": "<iso>", "notes": "",
      "world": {
        "start_map": 1001, "spawn": [45, 40], "exit_radius": 4,
        "maps": [
          {"mapid": 1001, "name": "永泽城", "minimap": 1001, "spawn": [45, 40],
           "npcs": [[1000, 1013, 47, 42, "村长"], [1001, 4427, 60, 55, "传送使"]],
           "exits": [["新月平原", 342, 19]],
           "links": [1006, 1002]}
        ]
      },
      "battle": {
        "hero_hp": 400, "enemy_count": 1, "enemy_name": "野狼", "enemy_shape": 2130,
        "enemy_hp": 100, "attack_dmg": 50, "enemy_attack_dmg": 20,
        "enemy_speed_base": 115, "enemy_speed_step": 5, "hero_speed": 100,
        "enemy_skills": [415], "enemy_skill_chance": 0.4,
        "enemy_hurt_bs_chance": 0.35, "turn_timeout": 30,
        "pet_hp": 350, "pet_mp": 380, "pet_fp": 30, "str_dmg_divisor": 4,
        "drop_chance": 0.6, "drop_max_per_battle": 1
      },
      "encounter": {"every": 5, "per_map": true},
      "shop": {"items": [[22100, "小麦种子", 100, 1], [10380, "鲜嫩草叶", 30, 1]]},
      "provenance": {"world": "真值（minimap_data / npcposdata / findpathdata）", "...": "..."}
    }

世界字段与 m4_server 常量对应关系：

    config                         m4_server 常量
    -------------------------      --------------------------------
    world.start_map                START_MAP_ID / MAP_ID / MINIMAP_ID
    world.spawn                    SPAWN_X, SPAWN_Y
    world.exit_radius              EXIT_RADIUS
    world.maps[i].name/minimap/    MAPS[mapid]['name'/'minimap'/'spawn'/'npcs']
      spawn/npcs
    world.maps[i].exits            MAP_EXITS[mapid]  （并反推 NAME2MAP）
    world.maps[i].links            MAP_LINKS[mapid]
    battle.*                       ENEMY_HP / ATTACK_DMG / ENEMY_ATTACK_DMG /
                                   ENEMY_SPEED_BASE / ENEMY_SPEED_STEP / HERO_SPEED /
                                   ENEMY_SKILLS / ENEMY_SKILL_CHANCE / TURN_TIMEOUT /
                                   HERO_HP / ENEMY_COUNT_DEFAULT / ENEMY_NAME / ENEMY_SHAPE /
                                   PET_HP / PET_MP / PET_FP / STR_DMG_DIVISOR /
                                   DROP_CHANCE / DROP_MAX_PER_BATTLE（见 BATTLE_MAP）
    progression.*                  EXP/per-level 曲线、HERO_TILI_MAX / HERO_HUOLI_MAX /
                                   LIFE_LV_* / LIFE_EXP_* / MAKE_COST_* / SELL_*（见 PROGRESSION_MAP）
    shop.items[i]                  SHOP_ITEMS（[造型, 名称, 单价, 限购数]，并重建 SHOP_PRICE / ITEM_NAMES）
    encounter.every                CLI 默认 --encounter-every
    encounter.per_map              ENCOUNTER_PER_MAP（true=按场景表出怪，城镇不遇怪；false=全图统一野狼）
    provenance（顶层，说明性）      不覆盖任何常量，仅记录每个配置键的出处标注

注意：`--enemies` / `--encounter-every` 等命令行参数**始终优先于**本配置；
本配置只提供默认值。世界数据（地图/NPC/出口/连通）则**无条件以本文件为准**。
"""
import json
import os
import time

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'world_config.json')
VERSION = 1

# battle 段字段 → m4_server 常量名
BATTLE_MAP = {
    'hero_hp': 'HERO_HP',
    'enemy_count': 'ENEMY_COUNT_DEFAULT',
    'enemy_name': 'ENEMY_NAME',
    'enemy_shape': 'ENEMY_SHAPE',
    'enemy_hp': 'ENEMY_HP',
    # ── 物伤公式参数（A 方案：反推的一个可行点，非真值）──
    # 改这几项会**真的**改变战斗伤害；默认值精确复现 50/48/20。
    'hero_at': 'HERO_AT', 'hero_df': 'HERO_DF', 'hero_dp': 'HERO_DP',
    'hero_level': 'HERO_LEVEL',
    'pet_at': 'PET_AT', 'pet_df': 'PET_DF', 'pet_dp': 'PET_DP',
    'pet_level': 'PET_LEVEL',
    'enemy_at': 'ENEMY_AT', 'enemy_df': 'ENEMY_DF', 'enemy_dp': 'ENEMY_DP',
    'enemy_level': 'ENEMY_LEVEL',
    'dmg_gain': 'DMG_GAIN', 'dmg_floor': 'DMG_FLOOR', 'dmg_p_div': 'DMG_P_DIV',
    'dmg_model': 'DMG_MODEL',
    'dmg_rng_lo': 'DMG_RNG_LO', 'dmg_rng_hi': 'DMG_RNG_HI',
    'enemy_speed_base': 'ENEMY_SPEED_BASE',
    'enemy_speed_step': 'ENEMY_SPEED_STEP',
    'enemy_speed_by_shape': 'ENEMY_SPEED_BY_SHAPE',
    'enemy_skills_by_shape': 'ENEMY_SKILLS_BY_SHAPE',
    'enemy_hurt_bs_by_shape': 'ENEMY_HURT_BS_BY_SHAPE',
    'hero_speed': 'HERO_SPEED',
    'enemy_skills': 'ENEMY_SKILLS',
    'enemy_skill_chance': 'ENEMY_SKILL_CHANCE',
    'enemy_hurt_bs_chance': 'ENEMY_HURT_BS_CHANCE',
    'turn_timeout': 'TURN_TIMEOUT',
    # ── 宠物面板 / 力量加成 / 掉落（2026-10-20 外置，此前硬编码在 m4_server）──
    'pet_hp': 'PET_HP', 'pet_mp': 'PET_MP', 'pet_fp': 'PET_FP',
    'str_dmg_divisor': 'STR_DMG_DIVISOR',
    'drop_chance': 'DROP_CHANCE', 'drop_max_per_battle': 'DROP_MAX_PER_BATTLE',
}
# 数值范围（含端点），越界即校验失败
BATTLE_RANGE = {
    'hero_hp': (1, 99999), 'enemy_count': (1, 20), 'enemy_hp': (1, 99999),
    # 造型是 2 字节字段（AddNpc / WarAdd 都按 '<H' 打）；0 或 >65535 会让客户端取不到美术。
    'enemy_shape': (1, 65535),
    'enemy_speed_base': (1, 9999), 'enemy_speed_step': (0, 999),
    'hero_speed': (1, 9999), 'enemy_skill_chance': (0.0, 1.0),
    'enemy_hurt_bs_chance': (0.0, 1.0),
    'turn_timeout': (1, 600),
    # 三围（攻击/防御/速度/法防/力量/魔力/魅力/敏捷/体质）在客户端 ITEM_ALL 中均为 TWORD（2 字节，0..65535）
    # 但伤害公式反编译版是在 0..255 量级下标定的（reverse 参数点 hero_at=39 等）。
    # 这里区分「战斗计算值」(0..255) 与「面板显示值」(0..65535)：
    #   battle 段的 *_at/*_df/*_dp/*_speed 字段映射到战斗计算常量（PET_AT 等），保持 0..255；
    #   面板显示值由真值公式动态计算（battle_formulas.py），不写入配置。
    'hero_at': (0, 255), 'hero_df': (0, 255), 'hero_dp': (0, 255),
    'pet_at': (0, 255), 'pet_df': (0, 255), 'pet_dp': (0, 255),
    'enemy_at': (0, 255), 'enemy_df': (0, 255), 'enemy_dp': (0, 255),
    'hero_level': (1, 120), 'pet_level': (1, 120), 'enemy_level': (1, 120),
    'dmg_gain': (1.0, 3.0), 'dmg_floor': (0.0, 1.0), 'dmg_p_div': (1.0, 1000.0),
    'dmg_rng_lo': (0.0, 2.0), 'dmg_rng_hi': (0.0, 2.0),
    # 宠物面板三值（PET_MP/PET_FP 只是上限，允许 0）、力量加成除数、掉落参数
    'pet_hp': (1, 99999), 'pet_mp': (0, 99999), 'pet_fp': (0, 99999),
    'str_dmg_divisor': (1, 1000),
    'drop_chance': (0.0, 1.0), 'drop_max_per_battle': (0, 100),
}
# 取浮点（0~1 概率）的字段；其余按整数处理
BATTLE_FLOAT_KEYS = ('enemy_skill_chance', 'enemy_hurt_bs_chance',
                     'dmg_gain', 'dmg_floor', 'dmg_p_div',
                     'dmg_rng_lo', 'dmg_rng_hi', 'drop_chance')
# 字符串字段：原样透传，不做 int()/float() 转换
BATTLE_STR_KEYS = ('enemy_name', 'dmg_model')

# ────────────────────── progression 段：经验/金钱曲线（全部 ★SIM）──────────
# 为什么单列一段而不是塞进 battle：这些是**养成经济**参数，与战斗公式无关；
# 混在一起会让「改战斗手感」变成「顺手改了经济曲线」。
#
# ★★ 这一整段都是模拟参数（SIM），没有真值来源，别再去找：
#   ① 客户端无 expdata —— 485 脚本全库 0 命中；升级所需经验是服务端下发的
#      属性 ITEM_ALL[9]=m_NextExp，客户端只画不写（dlgstate 只读 g_Prop.m_NextExp）
#   ② 录像无经验 —— .mwr 只存战斗帧（实测 198 份语料 op 只有 112-125/129），
#      op17(PropChange) 一次都没出现，所以拿不到 m_Exp/m_NextExp
#   ③ 资料全库无表 —— 823KB 文本里「升级经验」只有 1 处道具文案，无等级-经验表
#   结论：只能接受模拟曲线，或按手感自调。改这里就是改升级节奏。
PROGRESSION_MAP = {
    'exp_per_enemy': 'BATTLE_EXP_PER_ENEMY',   # 每击败一只怪获得经验
    'exp_init': 'EXP_INIT',                    # 新角色初始经验
    'next_exp_init': 'NEXT_EXP_INIT',          # 1 级升 2 级所需经验
    'exp_per_level_step': 'EXP_PER_LEVEL_STEP',  # 每级递增步长（需求 = step × 当前等级）
    'point_per_level': 'POINT_PER_LEVEL',      # 每级发放可分配点数
    'grade_max': 'GRADE_MAX',                  # 等级上限
    'coin_init': 'COIN_INIT',                  # 新角色初始金钱
    'task_reward_coins': 'TASK_REWARD_COINS',  # 新手任务金钱奖励
    'task_reward_exp': 'TASK_REWARD_EXP',      # 新手任务经验奖励
    'tili_init': 'HERO_TILI_INIT',             # 体力初值（制作/战斗共用，见 PROP_IDX_TILI）
    'huoli_init': 'HERO_HUOLI_INIT',           # 活力初值（制作消耗，见 PROP_IDX_HUOLI）
    'battle_tili_regen': 'BATTLE_TILI_REGEN',  # 战斗胜利恢复体力
    # ── 上限 / 生活技能 / 制作成本 / 收购价（2026-10-20 外置，此前硬编码）──
    'tili_max': 'HERO_TILI_MAX',               # 体力上限（原服无真值）
    'huoli_max': 'HERO_HUOLI_MAX',             # 活力上限（原服无真值）
    'life_lv_init': 'LIFE_LV_INIT',            # 起始工艺等级
    'life_lv_max': 'LIFE_LV_MAX',              # 工艺等级上限（与 craftdata 最高 lv 对齐）
    'life_exp_init': 'LIFE_EXP_INIT',          # 起始工艺熟练度
    'life_exp_max': 'LIFE_EXP_MAX',            # 工艺熟练度上限
    'make_cost_huoli': 'MAKE_COST_HUOLI',      # 每件制作消耗活力（craftdata 缺 cost 兜底）
    'make_cost_money': 'MAKE_COST_MONEY',      # 每件制作消耗金钱
    'make_cost_sklv': 'MAKE_COST_SKLV',        # 面板显示所需技能等级（缺 lv 兜底）
    'sell_ratio_num': 'SELL_RATIO_NUM',        # 收购价 = 售价 × 分子 / 分母
    'sell_ratio_den': 'SELL_RATIO_DEN',
    'sell_price_min': 'SELL_PRICE_MIN',        # 收购价下限
    'sell_price_fallback': 'SELL_PRICE_FALLBACK',  # 未上架商品的收购价
}
PROGRESSION_RANGE = {
    'exp_per_enemy': (0, 1000000),
    'exp_init': (0, 100000000),
    'next_exp_init': (1, 1000000),
    'exp_per_level_step': (1, 1000000),
    'point_per_level': (0, 100),
    'grade_max': (1, 120),          # 本服 guardrail；原服资料 140（飞升前）/150（飞升后）
    'coin_init': (0, 2000000000),
    'task_reward_coins': (0, 100000000),
    'task_reward_exp': (0, 100000000),
    'tili_init': (0, 65535),        # TWORD 2B
    'huoli_init': (0, 65535),       # TWORD 2B
    'battle_tili_regen': (0, 65535),
    'tili_max': (0, 65535),         # TWORD 2B
    'huoli_max': (0, 65535),        # TWORD 2B
    'life_lv_init': (1, 120),       # 起始工艺等级；120 = craftdata 最高配方 lv，不是客户端等级上限
    'life_lv_max': (1, 120),        # 同上；原服生活技能上限随人物等级（资料只写“不超过人物等级”）
    'life_exp_init': (0, 1000000),
    'life_exp_max': (0, 1000000),
    'make_cost_huoli': (0, 65535),  # 活力是 TWORD
    'make_cost_money': (0, 2000000000),
    'make_cost_sklv': (0, 120),
    'sell_ratio_num': (0, 1000),
    'sell_ratio_den': (1, 1000),    # 分母不能为 0（sell_price_of 会做整除）
    'sell_price_min': (0, 2000000000),
    'sell_price_fallback': (0, 2000000000),
}
# 值是「整数→整数」字典的字段（JSON 里键是字符串，apply 时转回 int）
BATTLE_DICT_KEYS = ('enemy_speed_by_shape',)
DICT_RANGE = (1, 99999)      # 键/值的合法区间
# 值是「造型 → 技能 id 数组」的字段（JSON 键为字符串，apply 时转回 int）
BATTLE_SHAPE_LIST_KEYS = ('enemy_skills_by_shape',)
# 值是「造型 → 概率」的字段（0~1 浮点）
BATTLE_FLOAT_DICT_KEYS = ('enemy_hurt_bs_by_shape',)

# ────────────────────── 出处标注（provenance）──────────────────────
# 为什么单列一段而不是给每个键加注释：**JSON 不能写注释**——写进去 json.load
# 直接解析失败，整份 world_config.json 会被跳过（回归 _test_editor 的「坏 JSON」
# 分支正是这条）。所以「这个值是录像是模拟还是真值」这类说明只能随配置落进
# json 顶层的一个字符串字典 `provenance`。
#
# 口径：键 = 配置键（battle / progression 的键名，以及 world / encounter / shop
#       三个**段级**条目）；值 = 中文标注。
# `apply_to()` **不应用**本段——它纯说明性，不覆盖任何 m4_server 常量；
# `validate()` 把它当**可选**段校验（缺省不报错，老配置仍可用）。
# 回归 `tests\_test_provenance.py` 会断言「每个配置键都有标注，一个都不许漏」，
# 覆盖性检查请走下面的 provenance_gaps()。
_SIM = '模拟｜真值待补'
_SIM_REVERSE = '模拟｜真值待补（reverse 可行点，47 条录像可精确复现 50/48/20）'
PROVENANCE_SECTIONS = ('world', 'encounter', 'shop')
PROVENANCE = {
    # ── 段级 ──
    'world': '真值（minimap_data / npcposdata / findpathdata）',
    'encounter': '模拟｜遇怪步数真值待补（原服不可得）；per_map 怪物名单是真值（资料全库场景表×monsterdata），每场数量/权重/HP/速度仍是模拟',
    'shop': '结构真值（itemdata 造型/名称）＋ 价格模拟｜真值待补（itemdata 无价格；milldata.maxprice 是工厂自定价上限，不是 NPC 售价）',
    # ── battle 段 ──
    'hero_hp': '半真值（录像）',
    'enemy_count': '半真值（录像）',
    'enemy_name': '半真值（录像）',
    'enemy_shape': '半真值（录像）',
    'enemy_hp': '模拟｜录像 WAR_DEL 前累计伤害 50/98/146/148（14 场），无法区分死亡/逃跑，不能反推固定 HP；100 只复现多数 3 击序列',
    'hero_at': _SIM_REVERSE, 'hero_df': _SIM_REVERSE, 'hero_dp': _SIM_REVERSE,
    'hero_level': _SIM_REVERSE,
    'pet_at': _SIM_REVERSE, 'pet_df': _SIM_REVERSE, 'pet_dp': _SIM_REVERSE,
    'pet_level': _SIM_REVERSE,
    'enemy_at': _SIM_REVERSE, 'enemy_df': _SIM_REVERSE, 'enemy_dp': _SIM_REVERSE,
    'enemy_level': _SIM_REVERSE,
    'dmg_gain': '待复核（原标“反编译”；本仓库全部客户端二进制内未找到 1.3 / 0.1 常数实体）',
    'dmg_floor': '待复核（原标“反编译”；本仓库全部客户端二进制内未找到 1.3 / 0.1 常数实体）',
    'dmg_p_div': '待复核（原标“反编译”；本仓库全部客户端二进制内未找到 1.3 / 0.1 常数实体）',
    'dmg_model': '模型名（reverse / linear），非数值',
    'dmg_rng_lo': '模拟｜固定 1.0（47 条录像同源伤害零方差）',
    'dmg_rng_hi': '模拟｜固定 1.0（47 条录像同源伤害零方差）',
    'enemy_speed_base': _SIM, 'enemy_speed_step': _SIM, 'hero_speed': _SIM,
    'enemy_skill_chance': '模拟｜旧语料观测企鹅 27% / 野狼 82%，样本小且受技能类型影响；0.4 为本服折中',
    'enemy_hurt_bs_chance': _SIM, 'turn_timeout': _SIM,
    'enemy_speed_by_shape': '半真值（录像：企鹅 115 / 野狼 40）',
    'enemy_skills_by_shape': '真值（资料库＋录像：1→1103、2130→415）',
    'enemy_hurt_bs_by_shape': '半真值｜190 份有效录像中敌方仅 2130：45 命中/17 会心=37.8%，Wilson 95% CI≈[25%,52%]，与全局 0.35 不可区分；不是“野狼必红”',
    'enemy_skills': '真值（petskill 415）',
    'pet_hp': '半真值（录像区间 (307,364]，取 350）',
    'pet_mp': '半真值（宠物面板截图）',
    'pet_fp': '半真值（宠物面板截图）',
    'str_dmg_divisor': '本服约定（非真值）',
    'drop_chance': '模拟｜资料库只写“哪个场景掉什么”，无概率；0.6 为本服约定',
    'drop_max_per_battle': '模拟｜同上；每场 1 件为本服约定',
    # ── progression 段（除 grade_max 外整段模拟，见 PROGRESSION_MAP 上方证据）──
    'exp_per_enemy': _SIM, 'exp_init': _SIM, 'next_exp_init': _SIM,
    'exp_per_level_step': _SIM, 'point_per_level': _SIM,
    'grade_max': '模拟｜本服现用 120；资料库：飞升前上限 140、飞升后 150（docx 30354/31165）；客户端 m_Grade/m_GradeLimit 为 TBYTE。取 120 的理由：主线 7–22 的真值等级门槛为 35–120（一览表），原 30 会让剧情 7 以后永远接不到；120 既是现有 guardrail 上限，也是本库最高真值门槛（剧情 22 ≥120）',
    'coin_init': '模拟｜资料库无初始金钱数值；50000 为本服约定',
    'task_reward_coins': '模拟｜资料库只写任务给“经验/金钱”，无具体数值；500 为本服约定',
    'task_reward_exp': '模拟｜同上；200 为本服约定',
    'tili_init': _SIM, 'huoli_init': _SIM, 'battle_tili_regen': _SIM,
    'tili_max': '模拟｜客户端只有 m_Tili_Max/m_Huoli_Max 属性槽（TWORD），上限 100 无资料依据',
    'huoli_max': '模拟｜同上；上限 100 无资料依据',
    'life_lv_init': '模拟｜起始 5（本服为让首档配方可用而设；资料库无初始工艺等级）',
    'life_lv_max': '半真值｜120 = 客户端 craftdata 最高配方 lv（25248）；原服生活技能上限随人物等级，无独立数值',
    'life_exp_init': _SIM, 'life_exp_max': _SIM,
    'make_cost_huoli': '真值兜底｜craftdata 每条配方自带 cost(16–58)，已优先使用；仅缺 cost 时回落到 10（48 条均不缺，不触发）',
    'make_cost_money': '模拟｜craftdata 只有活力 cost，没有金钱费用；100 金/件为本服约定',
    'make_cost_sklv': '真值兜底｜craftdata 每条配方自带 lv(5–120)，已优先使用；仅缺 lv 时回落到 1（48 条均不缺，不触发）',
    'sell_ratio_num': '模拟｜itemdata 无价格，资料库无收购折扣；1/4 为本服约定',
    'sell_ratio_den': '模拟｜同上；1/4 为本服约定',
    'sell_price_min': '模拟｜未上架商品按 1 金收购（本服约定）',
    'sell_price_fallback': '模拟｜同上；1 金（本服约定）',
}


def provenance_gaps(prov=None):
    """返回 PROVENANCE 相对「应有覆盖集」漏掉的键集合（空集 = 全覆盖）。

    应有覆盖集 = BATTLE_MAP 全部键 ∪ PROGRESSION_MAP 全部键 ∪ 三个段级键。
    抽成函数是为了**可做负向验证**：测试里删掉一个键后必须能被本函数报出来。
    """
    prov = PROVENANCE if prov is None else prov
    required = set(BATTLE_MAP) | set(PROGRESSION_MAP) | set(PROVENANCE_SECTIONS)
    return required - set(prov or ())


def _m4():
    """延迟导入 m4_server（避免与 m4_server 的 import 形成环路）。"""
    import m4_server
    return m4_server


# ────────────────────────────── 生成 / 读写 ──────────────────────────────
def default_config(m=None):
    """从 m4_server 的当前默认值反推一份配置（首次生成即等价现状）。"""
    m = m or _m4()
    maps = []
    for mapid in sorted(m.MAPS):
        d = m.MAPS[mapid]
        maps.append({
            'mapid': int(mapid),
            'name': d['name'],
            'minimap': int(d.get('minimap', mapid)),
            'spawn': [int(v) for v in d['spawn']],
            'npcs': [[int(t[0]), int(t[1]), int(t[2]), int(t[3]), t[4]]
                     for t in d.get('npcs', [])],
            'exits': [[t[0], int(t[1]), int(t[2])] for t in m.MAP_EXITS.get(mapid, [])],
            'links': [int(v) for v in m.MAP_LINKS.get(mapid, [])],
        })
    battle = {}
    for key, const in BATTLE_MAP.items():
        val = getattr(m, const)
        if isinstance(val, dict):
            battle[key] = {str(k): v for k, v in val.items()}
        else:
            battle[key] = list(val) if isinstance(val, (list, tuple)) else val
    # progression：整段 SIM（经验/金钱曲线），见 PROGRESSION_MAP 上方说明
    progression = {}
    for key, const in PROGRESSION_MAP.items():
        progression[key] = int(getattr(m, const))
    # shop：把 m4_server.SHOP_ITEMS 当前完整快照（含 import 期 MATERIAL_SHOP 追加）外置。
    #   快照口径见文件头「shop.items」；apply 时会按本快照原地覆盖 SHOP_ITEMS 与派生索引。
    shop_items = [[int(shape), name, int(price), int(qty)]
                  for (shape, name, price, qty) in m.SHOP_ITEMS]
    return {
        'version': VERSION,
        'updated': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'notes': '由编辑器从 m4_server 默认值生成；删除本文件即回到内置默认值。',
        'world': {
            'start_map': int(m.START_MAP_ID),
            'spawn': [int(m.SPAWN_X), int(m.SPAWN_Y)],
            'exit_radius': int(m.EXIT_RADIUS),
            'maps': maps,
        },
        'battle': battle,
        'progression': progression,
        'encounter': {'every': int(m.ENCOUNTER_EVERY_DEFAULT)
                      if hasattr(m, 'ENCOUNTER_EVERY_DEFAULT') else 5,
                      'per_map': bool(getattr(m, 'ENCOUNTER_PER_MAP', True))},
        'shop': {'items': shop_items},
        # provenance 纯说明性：不改任何常量，apply_to 也不读它（见其定义处说明）。
        'provenance': dict(PROVENANCE),
    }


def load_config(path=None):
    """读配置；不存在/损坏返回 None（调用方保持默认值）。"""
    return probe_config(path)[0]


def probe_config(path=None):
    """读配置并**区分失败原因**：返回 (cfg 或 None, reason, detail)。

    为什么单独一个函数：`load_config` 把「文件不存在」「JSON 坏了」「根不是对象」
    三种情况都压成 None，调用方于是只能回一句「无 world_config.json」——
    文件明明在、只是写坏了，也会被当成「没配」。启动日志要能一眼看出是哪一种。

    reason 取值：
        'ok'       读到了 dict
        'missing'  文件不存在（合法状态：用内置默认）
        'invalid'  文件在，但读不出 / JSON 解析失败 / 根节点不是对象（**需要人管**）
        'version_stale'  文件能读、但 version 低于当前 VERSION（**已跳过**）
        'version_newer'  文件 version 高于当前 VERSION（程序太旧，**已跳过**）

    ★ 2026-10-20 加版本校验：配置结构会随代码演进（加字段、改语义）。
      旧版配置缺新字段时，`apply_to` 里的 setattr 循环会静默跳过那些键，
      结果「文件在、应用了、但一半参数没生效」——比直接跳过更难查。
      所以版本落后一律**整体跳过**，并在横幅里明说，让用户去编辑器重新生成。

    ★ 用 os.path.isfile 判断存在性而不是靠异常类型：Windows 上「目录同名」
      「权限拒绝」都会抛 OSError，但它们不是「没配」，必须如实报出来。
    """
    p = path or CONFIG_PATH
    if not os.path.isfile(p):
        return None, 'missing', p
    try:
        with open(p, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except OSError as e:
        return None, 'invalid', '读取失败：%r' % (e,)
    except ValueError as e:
        return None, 'invalid', 'JSON 解析失败：%s' % (e,)
    if not isinstance(cfg, dict):
        return None, 'invalid', '根节点不是对象（是 %s）' % type(cfg).__name__
    # ★ 版本校验（2026-10-20）：落后或超前都整体跳过，不做部分应用。
    ver = cfg.get('version')
    if isinstance(ver, int):
        if ver < VERSION:
            return None, 'version_stale', (
                '配置 version=%d，当前需要 %d；已跳过（用编辑器重新生成即可）'
                % (ver, VERSION))
        if ver > VERSION:
            return None, 'version_newer', (
                '配置 version=%d，比本程序（%d）新；已跳过（请升级程序）'
                % (ver, VERSION))
    return cfg, 'ok', p


def save_config(cfg, path=None):
    """校验并写配置（先备份旧文件）。返回 (ok, errors)。"""
    errors = validate(cfg)
    if errors:
        return False, errors
    path = path or CONFIG_PATH
    try:
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                old = f.read()
            with open(path + '.bak', 'w', encoding='utf-8') as f:
                f.write(old)
        cfg = dict(cfg)
        cfg['version'] = VERSION
        cfg['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        return True, []
    except OSError as e:
        return False, ['写入失败: %s' % e]


# ────────────────────────────── 校验 ──────────────────────────────
def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate(cfg):
    """返回错误字符串列表；空列表 = 通过。"""
    errs = []
    if not isinstance(cfg, dict):
        return ['配置根必须是对象']

    world = cfg.get('world')
    if not isinstance(world, dict):
        errs.append('缺少 world 段')
        world = {}
    maps = world.get('maps')
    if not isinstance(maps, list) or not maps:
        errs.append('world.maps 必须是非空数组')
        maps = []

    ids, names = set(), {}
    for i, mp in enumerate(maps):
        tag = 'world.maps[%d]' % i
        if not isinstance(mp, dict):
            errs.append('%s 必须是对象' % tag)
            continue
        mid = mp.get('mapid')
        if not _is_int(mid):
            errs.append('%s.mapid 必须是整数' % tag)
            continue
        if mid in ids:
            errs.append('mapid %d 重复' % mid)
        ids.add(mid)
        if not isinstance(mp.get('name'), str) or not mp.get('name'):
            errs.append('%s.name 必须是非空字符串' % tag)
        else:
            names[mp['name']] = mid
        if mp.get('minimap') is not None and not _is_int(mp['minimap']):
            errs.append('%s.minimap 必须是整数' % tag)
        sp = mp.get('spawn')
        if not (isinstance(sp, list) and len(sp) == 2 and all(_is_int(v) for v in sp)):
            errs.append('%s.spawn 必须是 [x, y]' % tag)
        npcs = mp.get('npcs', [])
        if not isinstance(npcs, list):
            errs.append('%s.npcs 必须是数组' % tag)
            npcs = []
        for j, npc in enumerate(npcs):
            if not (isinstance(npc, list) and len(npc) >= 5
                    and _is_int(npc[0]) and _is_int(npc[1]) and _is_int(npc[2])
                    and _is_int(npc[3]) and isinstance(npc[4], str)):
                errs.append('%s.npcs[%d] 必须是 [id, shape, x, y, 名字]' % (tag, j))
        exits = mp.get('exits', [])
        if not isinstance(exits, list):
            errs.append('%s.exits 必须是数组' % tag)
            exits = []
        for j, ex in enumerate(exits):
            if not (isinstance(ex, list) and len(ex) == 3
                    and isinstance(ex[0], str) and _is_int(ex[1]) and _is_int(ex[2])):
                errs.append('%s.exits[%d] 必须是 [目标场景名, x, y]' % (tag, j))
        links = mp.get('links', [])
        if not isinstance(links, list) or not all(_is_int(v) for v in links):
            errs.append('%s.links 必须是 mapid 数组' % tag)

    # 出口目标名必须存在
    for mp in maps:
        if not isinstance(mp, dict):
            continue
        for ex in mp.get('exits', []) or []:
            if isinstance(ex, list) and ex and isinstance(ex[0], str) and ex[0] not in names:
                errs.append('地图「%s」的出口目标「%s」不存在对应地图' % (mp.get('name'), ex[0]))
    # links 必须指向存在的 mapid
    for mp in maps:
        if not isinstance(mp, dict):
            continue
        for lk in mp.get('links', []) or []:
            if _is_int(lk) and lk not in ids:
                errs.append('地图「%s」的连通目标 mapid=%s 不存在' % (mp.get('name'), lk))
    # 起始图必须存在
    sm = world.get('start_map')
    if not _is_int(sm) or (ids and sm not in ids):
        errs.append('world.start_map 必须是已存在的地图 id')

    battle = cfg.get('battle')
    if not isinstance(battle, dict):
        errs.append('缺少 battle 段')
        battle = {}
    # ★ 这三张「造型 → X」表是**可选覆盖表**：空对象或整键缺省都合法，
    #   语义 = 不按造型登记，运行时回落全局常量
    #   （ENEMY_SPEED_BASE / ENEMY_SKILLS / ENEMY_HURT_BS_CHANCE）。
    #   历史上这里要求「非空」，而 m4_server 的默认值恰恰是空表，
    #   于是 default_config() 生成的配置**永远存不进去**、编辑器整体失效。
    for key in BATTLE_DICT_KEYS:
        d = battle.get(key)
        if d is None:
            continue
        if not isinstance(d, dict):
            errs.append('battle.%s 必须是「造型→速度」对象' % key)
            continue
        lo, hi = DICT_RANGE
        for k, v in d.items():
            try:
                ki = int(k)
            except (TypeError, ValueError):
                errs.append('battle.%s 的键必须是造型 id（整数）' % key)
                break
            if not lo <= ki <= hi or not _is_int(v) or not lo <= v <= hi:
                errs.append('battle.%s 的 %s→%s 越界（%d~%d）' % (key, k, v, lo, hi))
                break
    for key in BATTLE_FLOAT_DICT_KEYS:
        d = battle.get(key)
        if d is None:
            continue
        if not isinstance(d, dict):
            errs.append('battle.%s 必须是「造型→概率」对象' % key)
            continue
        lo, hi = DICT_RANGE
        for k, v in d.items():
            try:
                ki = int(k)
            except (TypeError, ValueError):
                errs.append('battle.%s 的键必须是造型 id（整数）' % key)
                break
            if not lo <= ki <= hi:
                errs.append('battle.%s 的造型 %s 越界（%d~%d）' % (key, k, lo, hi))
                break
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= float(v) <= 1.0:
                errs.append('battle.%s 的 %s 必须是 0~1 的概率' % (key, k))
                break
    for key in BATTLE_SHAPE_LIST_KEYS:
        d = battle.get(key)
        if d is None:
            continue
        if not isinstance(d, dict):
            errs.append('battle.%s 必须是「造型→技能数组」对象' % key)
            continue
        lo, hi = DICT_RANGE
        for k, v in d.items():
            try:
                ki = int(k)
            except (TypeError, ValueError):
                errs.append('battle.%s 的键必须是造型 id（整数）' % key)
                break
            if not lo <= ki <= hi:
                errs.append('battle.%s 的造型 %s 越界（%d~%d）' % (key, k, lo, hi))
                break
            if not (isinstance(v, list) and v and all(_is_int(x) and 1 <= x <= hi for x in v)):
                errs.append('battle.%s 的 %s 必须是非空的技能 id 数组' % (key, k))
                break
    for key, (lo, hi) in BATTLE_RANGE.items():
        v = battle.get(key)
        if v is None:
            errs.append('battle.%s 缺失' % key)
            continue
        if key in BATTLE_FLOAT_KEYS:
            if not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                errs.append('battle.%s 必须在 [%s, %s]' % (key, lo, hi))
        elif not _is_int(v) or not lo <= v <= hi:
            errs.append('battle.%s 必须是 %d~%d 的整数' % (key, lo, hi))
    if not isinstance(battle.get('enemy_name'), str) or not battle.get('enemy_name'):
        errs.append('battle.enemy_name 必须是非空字符串')
    if battle.get('dmg_model') not in ('linear', 'reverse'):
        errs.append("battle.dmg_model 只能是 'linear' 或 'reverse'")
    sk = battle.get('enemy_skills')
    if not isinstance(sk, list) or not all(_is_int(v) for v in sk):
        errs.append('battle.enemy_skills 必须是招式 id 数组')

    enc = cfg.get('encounter')
    if not isinstance(enc, dict) or not _is_int(enc.get('every')) or enc['every'] < 0:
        errs.append('encounter.every 必须是 ≥0 的整数')
    # per_map 可选：缺省 = True（按场景表遇怪）。显式给了就必须是 bool。
    if isinstance(enc, dict) and 'per_map' in enc and not isinstance(enc['per_map'], bool):
        errs.append('encounter.per_map 必须是 true/false')

    # progression 段：可选（缺省 = 保留模块默认，兼容旧配置）。
    # 但**给了就必须整段合法**——半对的配置比没有更危险：玩家以为改生效了。
    prog = cfg.get('progression')
    if prog is not None:
        if not isinstance(prog, dict):
            errs.append('progression 必须是对象')
        else:
            for key, (lo, hi) in PROGRESSION_RANGE.items():
                if key not in prog:
                    continue
                v = prog[key]
                if not _is_int(v):
                    errs.append('progression.%s 必须是整数（实得 %r）' % (key, v))
                elif not (lo <= v <= hi):
                    errs.append('progression.%s 越界：%d 不在 [%d, %d]'
                                % (key, v, lo, hi))
            unknown = sorted(set(prog) - set(PROGRESSION_MAP))
            if unknown:
                errs.append('progression 有未知字段：%r' % (unknown,))

    # shop 段：**整段可选**（缺省 = 保留模块默认，口径同 progression）。
    # 本段是表类（商品清单 + 名称/价格），改的是 SHOP_ITEMS 及其派生索引。
    shop = cfg.get('shop')
    if shop is not None:
        if not isinstance(shop, dict):
            errs.append('shop 必须是对象')
        else:
            unknown = sorted(set(shop) - {'items'})
            if unknown:
                errs.append('shop 有未知字段：%r' % (unknown,))
            items = shop.get('items')
            if not isinstance(items, list) or not items:
                errs.append('shop.items 必须是非空数组')
            else:
                for j, it in enumerate(items):
                    if not (isinstance(it, list) and len(it) == 4
                            and _is_int(it[0]) and isinstance(it[1], str) and it[1]
                            and _is_int(it[2]) and _is_int(it[3])):
                        errs.append('shop.items[%d] 必须是 [造型, 名称, 单价, 单次限购数]' % j)

    # provenance 段：**整段可选**的说明性字符串字典（缺省不报错，老配置仍可用）。
    # 只校验「存在则必须是 str→str」，不做覆盖性断言——那是回归测试的职责。
    prov = cfg.get('provenance')
    if prov is not None:
        if not isinstance(prov, dict):
            errs.append('provenance 必须是对象')
        elif not all(isinstance(k, str) and isinstance(v, str) for k, v in prov.items()):
            errs.append('provenance 必须是「配置键→中文标注」的字符串字典')
    return errs


# ────────────────────────────── 应用到 m4_server ──────────────────────────────
def apply_to(m=None, path=None):
    """把配置覆盖到 m4_server 模块。返回 (applied: bool, message: str)。

    · 只做**覆盖**，不做校验失败后的部分应用 —— 校验不过就整体跳过（保证一致性）；
    · MAPS / MAP_EXITS / MAP_LINKS / NAME2MAP 原地 mutate（避免引用失效）。
    """
    m = m or _m4()
    cfg, reason, detail = probe_config(path)
    if reason == 'missing':
        return False, '无 world_config.json（使用内置默认值）：%s' % detail
    # ★ 版本不符（2026-10-20）：单独给消息，不要混进「读不出」。
    #   两种情况都要让用户知道「文件被跳过了、现在跑的是内置默认」。
    if reason == 'version_stale':
        return False, '配置版本落后，已跳过：%s' % detail
    if reason == 'version_newer':
        return False, '配置版本比程序新，已跳过：%s' % detail
    if cfg is None:
        return False, 'world_config.json 存在但读不出，已忽略（需人工处理）：%s' % detail
    errs = validate(cfg)
    if errs:
        return False, '配置校验失败（文件在、内容不合法，已整体忽略）：%s' % '；'.join(errs[:5])
    world = cfg['world']

    m.MAPS.clear()
    for mp in world['maps']:
        m.MAPS[int(mp['mapid'])] = {
            'name': mp['name'],
            'minimap': int(mp.get('minimap', mp['mapid'])),
            'spawn': tuple(int(v) for v in mp['spawn']),
            'npcs': [(int(t[0]), int(t[1]), int(t[2]), int(t[3]), t[4])
                     for t in mp.get('npcs', [])],
        }
    m.MAP_EXITS.clear()
    for mp in world['maps']:
        m.MAP_EXITS[int(mp['mapid'])] = [(t[0], int(t[1]), int(t[2]))
                                         for t in mp.get('exits', [])]
    m.MAP_LINKS.clear()
    for mp in world['maps']:
        m.MAP_LINKS[int(mp['mapid'])] = [int(v) for v in mp.get('links', [])]
    m.NAME2MAP.clear()
    for mp in world['maps']:
        m.NAME2MAP[mp['name']] = int(mp['mapid'])

    m.START_MAP_ID = int(world['start_map'])
    m.MAP_ID = m.START_MAP_ID
    m.MINIMAP_ID = m.START_MAP_ID
    m.SPAWN_X, m.SPAWN_Y = (int(v) for v in world['spawn'])
    m.EXIT_RADIUS = int(world['exit_radius'])

    for key, const in BATTLE_MAP.items():
        # 可选覆盖表（「造型 → 速度/技能/会心」）允许整键缺省：缺省 = 保留模块默认，
        # 与 validate() 的口径一致。缺了还硬取会抛 KeyError，让整份配置被静默丢弃。
        if key not in cfg['battle']:
            continue
        val = cfg['battle'][key]
        if key in BATTLE_DICT_KEYS:
            val = {int(k): int(v) for k, v in val.items()}
        elif key in BATTLE_SHAPE_LIST_KEYS:
            val = {int(k): [int(x) for x in v] for k, v in val.items()}
        elif key in BATTLE_FLOAT_DICT_KEYS:
            val = {int(k): float(v) for k, v in val.items()}
        elif key == 'enemy_skills':
            val = [int(v) for v in val]
        elif key in BATTLE_STR_KEYS:
            pass                              # 字符串原样透传（名称/模型名）
        else:
            val = float(val) if key in BATTLE_FLOAT_KEYS else int(val)
        setattr(m, const, val)

    # progression 段：**整段可选**。老配置（没有这一段）保留模块默认值，
    # 不因为缺段就整体跳过配置——那样会把 60 张地图一起丢掉。
    prog = cfg.get('progression')
    if isinstance(prog, dict):
        for key, const in PROGRESSION_MAP.items():
            if key not in prog:
                continue
            setattr(m, const, int(prog[key]))

    # shop 段：**整段可选**（缺省保留模块默认，口径同 progression）。
    # · SHOP_ITEMS 必须**原地**改（切片赋值），否则购买/制作路径里早已捕获的
    #   引用会指向旧表；这与 MARKEXITS 的 clear()+填充同一思路。
    # · 名称/价格索引是从旧 SHOP_ITEMS 早绑定生成的，覆盖后必须原地重建，
    #   否则日志与 item_name_of() 还是旧名字（应是「物品10155」那类回归）。
    # · m4_server import 期用 MATERIAL_SHOP 追加过材料并 update 索引——那是既有
    #   行为，不改；本函数在其之后运行，按配置快照覆盖，两者不冲突。
    # ★ 必须 hasattr 守卫：m4_server 的 import 期 apply_to 那条调用（模块导入到一半时
    #   执行）**早于** SHOP_ITEMS / SHOP_PRICE / ITEM_NAMES 的定义——属性还没建出来，
    #   直接取会抛 AttributeError，被 m4_server 的 except 吞成「配置层异常，已跳过」，
    #   结果连 60 张地图一起丢。缺属性时跳过（与 progression 缺段同一口径）；
    #   模块加载完毕后由 reload_world_config 走同一函数，那时才真正覆盖 shop。
    shop = cfg.get('shop')
    if (isinstance(shop, dict) and isinstance(shop.get('items'), list)
            and hasattr(m, 'SHOP_ITEMS') and hasattr(m, 'SHOP_PRICE')
            and hasattr(m, 'ITEM_NAMES')):
        m.SHOP_ITEMS[:] = [(int(shape), name, int(price), int(qty))
                           for (shape, name, price, qty) in shop['items']]
        m.SHOP_PRICE.clear()
        m.SHOP_PRICE.update({shape: price for (shape, _n, price, _q) in m.SHOP_ITEMS})
        m.ITEM_NAMES.clear()
        m.ITEM_NAMES.update({shape: name for (shape, name, _p, _q) in m.SHOP_ITEMS})

    m.ENCOUNTER_EVERY_DEFAULT = int(cfg['encounter']['every'])
    # ★ 2026-10-22：场景遇怪开关（缺省 true）。老配置没有这个键 → 保持默认开启。
    if 'per_map' in cfg['encounter']:
        m.ENCOUNTER_PER_MAP = bool(cfg['encounter']['per_map'])
    # ★ 版本戳（2026-10-20）：记录第几次加载、何时加载。
    #   热加载的判据：编辑器保存后 compare 这个戳与 m4_server 端记录的戳，
    #   不同则说明文件变了、需要重载。setattr 是因为 m 可能是模块对象。
    setattr(m, 'WORLD_CONFIG_VERSION',
             getattr(m, 'WORLD_CONFIG_VERSION', 0) + 1)
    setattr(m, 'WORLD_CONFIG_UPDATED', cfg.get('updated', '?'))
    return True, '已应用 world_config.json（%d 张地图，updated=%s，v%d）' % (
        len(m.MAPS), cfg.get('updated', '?'),
        getattr(m, 'WORLD_CONFIG_VERSION', 0))