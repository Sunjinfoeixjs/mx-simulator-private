# -*- coding: utf-8 -*-
u"""角色最终属性：**唯一计算入口**（P3-01）。

设计要点：**派生值只算不存**
----------------------------
角色属性 = 基础 + 等级成长 + 加点 + 装备 的**纯函数结果**。
任何地方都不做 `self.atk += 装备加成` 这种累加：

  · 累加式实现在「重复穿同一件装备」「重登重放装备帧」「读档后再 apply 一次」
    这三种场景下必然叠加，而且叠加后无法反推哪一份是脏的；
  · 纯函数每次从**状态**（等级/五维/装备槽）重算，算 100 遍结果一样，
    「重复加载装备导致属性叠加」在结构上不可能发生；
  · 存档只需存状态（等级/五维/装备在背包里的 pos），**不需要存派生值**，
    旧档天然兼容（新字段一个都没有）。

真值 / 模拟 / 缺口
------------------
  A 真值   `BASE`：atk=39 / df=15 / dp=4 / hp=400。
           来自 47 条录像的伤害校核（A=39、敌方 D=0 → ⌊1.3×39⌋=50），
           数值本身已写进 `world_config.provenance`，可由配置覆盖。
  C 约定   `str_attack_bonus()`：力量每比初始高 4 点 +1 伤害。
           这是**本服约定**（原服公式在服务端，本库无数据），故不计入 atk，
           而是作为独立一项附加在伤害公式**之外**——若混进 atk 会被 1.3 倍放大。
  S 模拟   `LEVEL_GROWTH` / `SIM_ATTR_RULES`：默认**全 0**（即关闭）。
           原服的等级成长曲线与「体质→气血」换算都在服务端，本库无数据。
           默认 0 的好处：当前伤害/血量与录像校核值**逐字节不变**，
           想启用模拟成长只需改常量，不必动调用方。
  G 缺口   `equipment` 参数收的是装备 shape 列表，但装备加成表
           （`equipment_rules.EQUIP_STATS`）**为空**——客户端 itemdata 没有
           任何攻防数值字段。故装备目前对属性的贡献恒为 0，且如实标注，
           而不是编一组假数值让它"看起来生效"。
"""
import equipment_rules as eqr

# ── 五维下标（与 m4_server.ATTR_ORDER 同源；测试断言两边一致）──────────────
# ★ 客户端 dlgstate.ATTR_POINTS 的顺序是 体/力/魔/魅/敏，不是「力量在前」。
ATTR_INDEX = {'con': 36, 'str': 32, 'mag': 33, 'cha': 34, 'dex': 35}
ATTR_BASE = 10                 # 五维初始值（加点从 10 起算，见 m4_server.ATTR_BASE）

# ── 基础值（A 真值，可被 world_config 覆盖）────────────────────────────
BASE = {'atk': 39, 'df': 15, 'dp': 4, 'hp_max': 400, 'mp_max': 100, 'speed': 100}

# ── 等级成长（S 模拟，默认关闭）──────────────────────────────────────
# 每级增量。全 0 = 与原校核值完全一致；启用即整体切换成「模拟成长」，
# 届时伤害会偏离录像基线，必须同步更新 world_config.provenance 的标注。
LEVEL_GROWTH = {'atk': 0, 'df': 0, 'dp': 0, 'hp_max': 0, 'mp_max': 0, 'speed': 0}

# ── 加点换算（str 那一条是 C 约定，其余是 S 模拟且默认 0）────────────────
STR_PER_ATK = 4                # 力量每 +4 点 → 普攻 +1（本服约定，非真值）
SIM_ATTR_RULES = {             # 属性 key → {最终属性 key: 每点增量}
    'con': {'hp_max': 0},      # 体质 → 气血上限（SIM，默认 0）
    'mag': {'mp_max': 0},      # 魔力 → 魔法上限（SIM，默认 0）
    'dex': {'speed': 0},       # 敏捷 → 速度（SIM，默认 0）
    'cha': {},                 # 魅力：本服无对应战斗属性
}


def _attr(attrs, key, default=ATTR_BASE):
    """从 {下标: 值} 里取某维；缺失按初始值算（旧档/未初始化都安全）。"""
    if not attrs:
        return default
    return int(attrs.get(ATTR_INDEX[key], default))


def str_attack_bonus(attrs, base=ATTR_BASE, per=STR_PER_ATK):
    """力量加成：⌊(力量−初始)/4⌋，不低于 0。

    ★ 整数除法必须保持 `//` 语义：改成浮点再取整会在 3/7/11… 这些点上漂 1 点，
      而录像里普攻恒为 50，任何漂移都会让 `_test_damage` 判红。
    """
    if per <= 0:
        return 0
    return max(0, _attr(attrs, 'str') - base) // per


def equip_bonus(equipment):
    """装备加成求和 → {属性: 值}。

    `equipment` 可传 shape 的可迭代对象（重复传同一 shape **不会**加倍：
    这里先 set 去重——同一槽位物理上也只能有一件）。
    ⚠ 当前 `equipment_rules.EQUIP_STATS` 为空（数值真值不可得），恒返回 {}。
    """
    out = {}
    if not equipment:
        return out
    for shape in set(int(s) for s in equipment):
        for k, v in (eqr.EQUIP_STATS.get(shape) or {}).items():
            out[k] = out.get(k, 0) + int(v)
    return out


def final_stats(base=None, grade=1, attrs=None, equipment=(), growth=None,
                attr_rules=None, explain=False):
    """算最终属性。

    :param base:       基础值 dict（默认 `BASE`；调用方传 m4_server 的实时常量，
                       这样 world_config 改过 hero_at/df/dp/hp 也能生效）
    :param grade:      人物等级（等级成长用；默认参数全 0 时不影响结果）
    :param attrs:      五维 {下标: 值}
    :param equipment:  已装备的 shape 集合（当前无加成，接口先留好）
    :param growth:     等级成长表（默认 `LEVEL_GROWTH`）
    :param attr_rules: 加点换算表（默认 `SIM_ATTR_RULES`）
    :param explain:    True 时额外返回 `contrib`（每项来源明细，供 GM 诊断）
    :return: {atk, df, dp, hp_max, mp_max, speed, str_bonus[, contrib]}
    """
    base = dict(BASE if base is None else base)
    growth = LEVEL_GROWTH if growth is None else growth
    attr_rules = SIM_ATTR_RULES if attr_rules is None else attr_rules
    lv = max(1, int(grade or 1))
    steps = lv - 1

    out = {}
    contrib = {}
    for key in ('atk', 'df', 'dp', 'hp_max', 'mp_max', 'speed'):
        b = int(base.get(key, 0))
        g = int(growth.get(key, 0)) * steps
        a = 0
        parts = [('base', b)]
        if g:
            parts.append(('level', g))
        # 加点：按 attr_rules 逐维累加（str 的伤害加成不走这里，见 str_bonus）
        for akey, rule in attr_rules.items():
            per = int(rule.get(key, 0))
            if per:
                delta = max(0, _attr(attrs, akey) - ATTR_BASE) * per
                if delta:
                    a += delta
        if a:
            parts.append(('attr', a))
        eb = int(equip_bonus(equipment).get(key, 0))
        if eb:
            parts.append(('equip', eb))
        out[key] = b + g + a + eb
        contrib[key] = parts

    # 力量加成**单独一项**：它是加在伤害公式之外的（不能进 atk，否则被 1.3 放大）
    out['str_bonus'] = str_attack_bonus(attrs)
    contrib['str_bonus'] = [('attr_str', out['str_bonus'])] if out['str_bonus'] else []
    if explain:
        out['contrib'] = contrib
    return out


def explain_lines(base=None, grade=1, attrs=None, equipment=()):
    """人类可读的属性来源说明（GM `attr` 命令用）。"""
    st = final_stats(base=base, grade=grade, attrs=attrs,
                     equipment=equipment, explain=True)
    lines = []
    for key in ('atk', 'df', 'dp', 'hp_max', 'mp_max', 'speed', 'str_bonus'):
        parts = st['contrib'].get(key) or []
        text = ' + '.join('%s %+d' % (n, v) for n, v in parts) or '0'
        lines.append(u'%-9s = %-5d  (%s)' % (key, st[key], text))
    if not eqr.EQUIP_STATS:
        lines.append(u'装备加成：无（%s）' % eqr.STAT_GAP)
    return lines


def provenance():
    """真值/SIM/缺口一览（写进文档与 GM 面板，避免口径漂移）。"""
    return {
        'base': 'A 真值（47 条录像校核：atk39/df15/dp4/hp400；world_config 可覆盖）',
        'level_growth': 'SIM（默认全 0 = 关闭；原服曲线在服务端，本库无数据）',
        'str_bonus': 'C 本服约定（力量每 +4 → 普攻 +1，加在公式之外）',
        'sim_attr': 'SIM（默认全 0 = 关闭）',
        'equip': 'GAP（%s）' % eqr.STAT_GAP,
        'repair': 'GAP（%s）' % eqr.REPAIR_GAP,
        'skill_require': 'GAP（%s）' % eqr.SKILL_GAP,
    }
