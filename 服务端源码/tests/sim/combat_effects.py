# -*- coding: utf-8 -*-
"""状态效果机制（buff / debuff）—— P3-03。

## 这个模块解决什么

P3-03 之前，服务端**一条状态都没有**。战斗里只有两个回合级标记：

    _round_defending   set  本回合谁在防御
    _round_protectors  dict 本回合谁替谁挡

「燃烧每回合掉血」「狂暴攻速翻倍」「恐怖强制防御」「水晶墙反射 250%」这些
全部不存在。于是任何一招「附带状态」的招式，在本服只能表现成「一次伤害 +
客户端弹个图标」，下一回合什么都没有 —— 状态时长、叠加、驱散、结算全丢。

本模块提供**机制**（施加/到期/叠加/驱散/查询/系数聚合）与**数值表**（转录）。

## 证据等级（逐条标，不混）

`buff_data.py` 是客户端真值表，但里面**只有玩家可见的一句说明**，没有参数表。
所以：

  · 状态 id / 名字 / 说明文字                     → **TRUE**（`buff_data`，直读）
  · 数值（「持续6回合」「+30%」「×2」）            → **PARTIAL**
    是从说明文字**转录**的，不是从参数表读的。每条 `EFFECT_MODS` 都带 `src`
    字段抄下原文，判断可回溯。
  · 说明里没写数字的效果（「大幅度提高」「攻击力降低」）→ **UNKNOWN**
    这类条目**不进** `EFFECT_MODS`，只登记在 `UNREADABLE` 里。
  · 「持续回合默认值」「叠加规则」等机制约定      → **C**（本服约定）

284 条状态里，说明带数字的 **34 条**，其中 **29 条可转录**（`EFFECT_MODS`），
**5 条不可转录**（`UNREADABLE`：等级门槛 / 只说「降低」/ 解除条件）。
另有 25 号原文没有数字（与 24 号同为隐身豁免族），一并登记进 `UNREADABLE`
以便「隐身族」不残缺；剩下的 250 条整片无数字，统一见 `UNREADABLE_BULK`。

## 与现有战斗的关系

**默认空转**：没有任何状态被施加时，`EffectSet.mods()` 全部返回 1.0（或 0 加成），
乘上去等于没乘 —— 所以接入后录像复现值（主角 50 / 宠物 48 / 敌 20）逐位不变。
状态只能通过 GM 指令或（后续）招式附带施加。
"""
from __future__ import division

import buff_data as bd


# ── 返回码（不抛异常，让调用方能如实上报）────────────────────────────
R_OK = 'ok'
R_UNKNOWN = 'unknown_effect'      # 状态 id 不在客户端表里
R_ALREADY = 'already_active'
R_ABSENT = 'not_active'
R_FULL = 'too_many_effects'

# 同时生效的状态上限。**C 级约定**：客户端未暴露上限，这里取 16 仅作防爆，
# 正常战斗远达不到；超限拒绝而不是静默丢弃，便于发现异常施加。
MAX_ACTIVE = 16

# 单场战斗的默认持续回合（状态说明没写回合时的兜底）。C 级约定。
DEFAULT_TURNS = 3

# 单状态层数硬上限（防爆；转录表里最高要求是 4 层：279 溺水）。C 级约定。
MAX_STACKS = 99

# ── 修正字段（全部是**乘区**，中性值 1.0；加法字段中性值 0.0）──────────
#   名称             中性值  含义
#   atk_mul          1.0    攻击倍率（物攻）
#   def_mul          1.0    防御倍率
#   spd_mul          1.0    速度倍率（影响行动序）
#   mag_mul          1.0    灵力倍率
#   dealt_mul        1.0    造成伤害总倍率
#   taken_mul        1.0    受到伤害总倍率
#   heal_mul         1.0    治疗/复活效果倍率
#   mag_dealt_mul    1.0    法术伤害倍率
#   cp_regen_mul     1.0    每回合控制点恢复倍率
#   cp_cost_mul      1.0    招式控制点消耗倍率
#   reflect          0.0    受到伤害后反射给攻击方的比例
#   crit_add         0.0    会心率加法
#   seal_add         0.0    封印命中加法
#   hp_set           0      把气血直接设为该值（0=不生效）
#   dot_pct_max      0.0    每回合按**气血上限**扣的比例
#   dot_pct_cur      0.0    每回合按**当前气血**扣的比例
#   share            0.0    受到伤害分摊给同伴的比例
#   force_defend     False  强制进入防御姿态（不消耗行动）
#   no_action        False  无法行动（跳过本回合）
_MUL_KEYS = ('atk_mul', 'def_mul', 'spd_mul', 'mag_mul', 'dealt_mul',
             'taken_mul', 'heal_mul', 'mag_dealt_mul', 'cp_regen_mul',
             'cp_cost_mul')
_ADD_KEYS = ('reflect', 'crit_add', 'seal_add', 'hp_set',
             'dot_pct_max', 'dot_pct_cur', 'share')
_FLAG_KEYS = ('force_defend', 'no_action')


def neutral():
    """中性修正（乘区 1.0 / 加法 0 / 标志 False）。"""
    d = {k: 1.0 for k in _MUL_KEYS}
    d.update({k: 0 for k in _ADD_KEYS})
    d['hp_set'] = 0
    d.update({k: False for k in _FLAG_KEYS})
    return d


# ── 数值转录表 ────────────────────────────────────────────────────
# 结构： eid -> {
#     'name'   : 状态名（与 buff_data 一致，生成时断言过）
#     'src'    : 说明原文里的**关键片段**（转录依据，可回溯）
#     'turns'  : 说明里写明的持续回合（None = 说明没写，走 DEFAULT_TURNS）
#     'stack'  : 'add'（可叠加）/ 'refresh'（刷新时长）/ 'ignore'（重复施加无效）
#     其余键   : 直接是修正字段
# }
#
# ★ 只收「说明里有明确数字」的条目。看到 `dealt_mul` 缺省不是遗漏，
#   而是原文只说「大幅度提高」—— 无法量化就不编。
EFFECT_MODS = {
    # ── 增益（说明里带明确百分比/倍数）──
    165: {'name': '仁心良术', 'heal_mul': 1.30,
          'src': '「使用治疗或复活类法术的效果提高30%」'},
    166: {'name': '灵感强化', 'cp_regen_mul': 1.50,
          'src': '「每回合控制点恢复数增加50%」'},
    167: {'name': '强化魔力', 'mag_dealt_mul': 1.20,
          'src': '「提高法术伤害结果20%」'},
    184: {'name': '狂暴', 'atk_mul': 2.0, 'spd_mul': 2.0, 'def_mul': 0.7,
          'src': '「攻击和速度为原来的2倍，防御为原来的7成」'},
    188: {'name': '水晶墙', 'taken_mul': 0.20, 'reflect': 2.50,
          'src': '「受到任何伤害降低为原来的20%，并将所受伤害的250%反射给攻击方」'},
    194: {'name': '武魂·绝杀', 'dealt_mul': 2.0, 'turns': 3,
          'src': '「3回合内，使用攻击技能伤害结果*2」'},
    195: {'name': '武魂·回魂', 'heal_mul': 2.0, 'turns': 3,
          'src': '「3回合内，使用治疗技能加血效果*2」'},
    196: {'name': '武魂·禁语', 'seal_add': 0.10, 'turns': 3,
          'src': '「3回合内，封印命中率及上限+10%」'},
    286: {'name': '仁义天下', 'atk_mul': 1.5, 'mag_mul': 1.5,
          'def_mul': 1.5, 'spd_mul': 1.5,
          'src': '「攻击，灵力，防御和速度增加50%」'},
    403: {'name': '野性', 'stack': 'add',
          'src': '「每次受击都增加自己的攻击修炼和法术修炼2级」',
          'note': '修炼等级 +2/次受击；修炼换算成属性需修炼表，本服未建模 → 只记层数'},
    418: {'name': '神力', 'atk_mul': 1.0,
          'src': '「攻击与法术修炼修炼+10」',
          'note': '原文是**修炼等级 +10**，不是攻击倍率；无修炼表 → 倍率不臆造'},
    419: {'name': '坚盾', 'def_mul': 1.0,
          'src': '「防御修炼与法抗修炼+10」',
          'note': '同上：修炼 +10，不是防御倍率'},

    # ── 持续伤害（DOT）──
    287: {'name': '灼热', 'dot_pct_max': 0.20,
          'src': '「每回合掉当前气血上限的20%，如果血量回复满或死亡，则状态消失」'},
    299: {'name': '灵魂附体', 'dot_pct_cur': 0.30, 'turns': 2,
          'src': '「2回合内，每回合掉当回合气血总量的30%来维持灵魂链接」'},
    501: {'name': '杀人毒针', 'dot_pct_max': 0.10, 'turns': 4,
          'src': '「4回合内，每回合掉气血上限的10%」'},
    511: {'name': '寒冰入侵', 'dot_pct_max': 0.02,
          'src': '「气血每回合减少2%」'},
    514: {'name': '寄生', 'dot_pct_max': 0.50,
          'src': '「每回合掉当前气血上限的50%，宿主在此状态死亡后，孵化傀儡兽」'},

    # ── 减益（说明里带明确百分比）──
    260: {'name': '最终诅咒', 'taken_mul': 1.15, 'spd_mul': 0.90,
          'src': '「受到任何类型伤害增加15%，速度减少10%」'},
    265: {'name': '讳疾忌医', 'heal_mul': 0.70,
          'src': '「使用治疗或复活类法术的效果降低30%」'},
    266: {'name': '灵感弱化', 'cp_regen_mul': 0.50,
          'src': '「每回合控制点恢复数减少50%」'},
    267: {'name': '弱化魔力', 'mag_dealt_mul': 0.80,
          'src': '「降低法术伤害结果20%」'},
    279: {'name': '溺水', 'stack': 'add',
          'src': '「每回合开始时减少20％，效果可叠加4次（用加法叠加…最低可被减至20%）」',
          'note': '加法叠加：每层 -20%，4 层 -80% → ×0.20；即 1-0.2*层数'},
    292: {'name': '黄龙之怒', 'atk_mul': 0.80, 'mag_mul': 0.80, 'spd_mul': 0.80,
          'src': '「速度、攻击、灵力分别降低20%」'},
    407: {'name': '流行性感冒', 'cp_cost_mul': 1.30,
          'src': '「使用招式控制点消耗增加30%」'},
    507: {'name': '乾元锁', 'dealt_mul': 0.50,
          'src': '「伤害降低50%」'},
    515: {'name': '灵魂链接', 'share': 0.50,
          'src': '「该单位所受到的伤害，会有50%被平均分摊其他灵魂链接的单位上」'},

    # ── 控制 / 特殊 ──
    27: {'name': '恐怖', 'force_defend': True, 'turns': 1, 'stack': 'refresh',
         'src': '「处于受惊吓的状态，强制防御1回合」'},
    288: {'name': '乱舞春秋', 'hp_set': 1, 'turns': 1, 'stack': 'refresh',
          'src': '「下回合目标血量变为1」'},
    157: {'name': '三花聚顶', 'turns': 6,
          'src': '「法术伤害结果大幅度提高，持续6回合」',
          'note': '「大幅度」无数字 → 只转录 6 回合，倍率**不臆造**'},
}

# 说明里**无法转录成修正**的条目。登记它们是为了「不假装覆盖」：
# 这些状态本服只有名字和说明，没有任何战斗效果。
# 其中 5 条（24/41/235/253/282）属于「含量化描述」集合；25 号原文**没有数字**
# （「终级隐身；…除非攻击者拥有感知能力；并可使用法术和特技」），
# 单独登记是因为它与 24 号是同一族（隐身豁免），不登记会让人误以为漏掉。
UNREADABLE = {
    24: '隐身；「除非攻击者拥有感知能力或高过隐身者10级」—— 等级比较，非加成',
    25: '终级隐身；「除非攻击者拥有感知能力；并可使用法术和特技」—— 无数字，'
        '与 24 同族的豁免条件，同样无法转录',
    41: '冥界波；「防御和抗法修炼大幅降低，…增加攻击修炼和法术修炼各30级」——'
        ' 修炼等级，无修炼表可换算',
    235: '天罗地网符；「闪避降低至0，且攻击力降低」—— 只说降低，无数值',
    253: '画地为牢；「闪避降低至0，且受到伤害增加」—— 只说增加，无数值',
    282: '终毒安达里士；「死亡倒地或将气血恢复至100%可解除」—— 解除条件，无周期数值',
}

# 说明里**没有数字**的条目（284 - 34 = 250 条）统一归此口径：
UNREADABLE_BULK = ('说明未写数值（如 20「燃烧；…每回合对承受者造成伤害」），'
                   '本服不臆造强度，只有名字与说明可用')


def is_known(eid):
    """状态 id 是否在客户端真值表里（284 条）。"""
    return bd.get(eid) is not None


def is_modeled(eid):
    """是否有可用的数值转录（28 条）。"""
    return int(eid) in EFFECT_MODS


def mods_of(eid):
    """单个状态的修正字典（未建模 → None）。"""
    return EFFECT_MODS.get(int(eid))


def name_of(eid):
    """状态名：优先用转录表（已对账），否则回落到 buff_data 的解析。"""
    m = EFFECT_MODS.get(int(eid))
    if m:
        return m['name']
    return bd.name(eid)


def describe(eid):
    """「#id 名字；原文」一行（GM/日志用）。"""
    if not is_known(eid):
        return '#%d 未知状态' % int(eid)
    eid = int(eid)
    tag = '' if is_modeled(eid) else '（有说明无机制）'
    return '#%d %s%s' % (eid, bd.get(eid), tag)


# ── 实例 ──────────────────────────────────────────────────────────
def new_instance(eid, turns=None, stacks=1, source=None):
    """造一条状态实例（纯数据，便于存/读与断言）。

    `turns` 为 None 时取转录表里写明的回合；转录表也没写 → `DEFAULT_TURNS`。
    """
    eid = int(eid)
    m = EFFECT_MODS.get(eid) or {}
    if turns is None:
        turns = m.get('turns')
    if turns is None:
        turns = DEFAULT_TURNS
    return {
        'eid': eid,
        'turns': max(0, int(turns)),
        'stacks': max(1, int(stacks)),
        'source': source,          # 施加者 war id（用于「谁放的」这类统计）
    }


class EffectSet(object):
    """一个战斗单位的活跃状态集合。

    ★ 一个 `M4Server` 里每个参战单位一个（主角 / 宠物 / 每只敌方），
      **不要**做成全局单表 —— 那会让「敌方身上的燃烧」和「主角身上的燃烧」
      共用一条计时，tick 一次两边一起掉。
    """

    def __init__(self, owner=None):
        self.owner = owner
        self._d = {}                # eid -> instance

    # ── 施加 / 移除 ───────────────────────────────────────────
    def add(self, eid, turns=None, stacks=1, source=None):
        """施加状态。返回 (ok, reason)。

        叠加规则来自转录表 `stack` 字段：
          'refresh'（默认）  已有则只刷新回合数，不累加层数
          'add'              层数累加（上限见转录表 note，本模块只累到 MAX_STACKS）
          'ignore'           已有则完全不动
        """
        eid = int(eid)
        if not is_known(eid):
            return False, R_UNKNOWN
        cur = self._d.get(eid)
        m = EFFECT_MODS.get(eid) or {}
        mode = m.get('stack') or 'refresh'
        if cur is not None:
            if mode == 'ignore':
                return False, R_ALREADY
            if mode == 'add':
                cur['stacks'] = min(MAX_STACKS, cur['stacks'] + max(1, int(stacks)))
            # refresh：只续时长（下面统一算）
            inst = new_instance(eid, turns, cur['stacks'], source)
            inst['stacks'] = cur['stacks']
            # 续时长取「现有与新值中的较大者」，避免短状态把长状态压短
            inst['turns'] = max(cur['turns'], inst['turns'])
            self._d[eid] = inst
            return True, R_OK
        if len(self._d) >= MAX_ACTIVE:
            return False, R_FULL
        self._d[eid] = new_instance(eid, turns, stacks, source)
        return True, R_OK

    def remove(self, eid):
        """移除状态。返回 True=确实移掉了。"""
        return self._d.pop(int(eid), None) is not None

    def clear(self):
        """清空（战斗收尾 / 换场）。"""
        self._d.clear()

    # ── 查询 ──────────────────────────────────────────────────
    def has(self, eid):
        return int(eid) in self._d

    def get(self, eid):
        return self._d.get(int(eid))

    def ids(self):
        return sorted(self._d)

    def active(self):
        """活跃实例列表（按 eid 升序，便于逐位断言）。"""
        return [self._d[k] for k in sorted(self._d)]

    def __len__(self):
        return len(self._d)

    def __contains__(self, eid):
        return int(eid) in self._d

    # ── 回合推进 ──────────────────────────────────────────────
    def tick(self):
        """回合结束：每条状态回合数 -1，到 0 的移除。

        返回**本回合到期**的实例列表（按 eid 升序）。
        为什么返回而不是自己发帧：到期往往要给客户端补一帧「状态消失」，
        而帧序由调用方（战投层）统一编排，机制层不碰协议。
        """
        expired = []
        for eid in sorted(self._d):
            inst = self._d[eid]
            inst['turns'] -= 1
            if inst['turns'] <= 0:
                expired.append(inst)
        for inst in expired:
            self._d.pop(inst['eid'], None)
        return expired

    # ── 系数聚合 ──────────────────────────────────────────────
    def mods(self):
        """把本集合所有状态的修正**聚合**成一个字典。

        乘区：逐项相乘（两个 +20% 法伤 = ×1.44，不是 ×1.4 —— 乘法叠加是本服约定，
              原文没有说明多状态如何叠加，故按最保守的乘法处理并在此标注）。
        加法：逐项相加。
        标志：任一为真即真。
        """
        out = neutral()
        for eid in sorted(self._d):
            m = EFFECT_MODS.get(eid)
            if not m:
                continue
            inst = self._d[eid]
            n = max(1, int(inst.get('stacks') or 1))
            for k in _MUL_KEYS:
                v = m.get(k)
                if v is None:
                    continue
                if k == 'cp_cost_mul' or k == 'cp_regen_mul':
                    out[k] *= float(v) ** n
                else:
                    out[k] *= float(v)
            for k in _ADD_KEYS:
                v = m.get(k)
                if v is None:
                    continue
                if k == 'hp_set':
                    out[k] = int(v) if not out[k] else min(out[k], int(v))
                else:
                    out[k] += float(v) * n
            for k in _FLAG_KEYS:
                if m.get(k):
                    out[k] = True
        # 溺水 279 的「加法叠加」是**属性递减**而非 layering 倍率，
        # 其效果等价于 —— 每层 -20%，4 层 ×0.20。单独在这里收口，
        # 因为它的语义（加法叠加后取剩余）与其他乘区不同，混在 _MUL_KEYS 里会算错。
        if 279 in self._d:
            n = min(4, max(1, int(self._d[279].get('stacks') or 1)))
            out['atk_mul'] *= max(0.20, 1.0 - 0.20 * n)
            out['mag_mul'] *= max(0.20, 1.0 - 0.20 * n)
            out['def_mul'] *= max(0.20, 1.0 - 0.20 * n)
        return out

    # ── 每回合结算 ────────────────────────────────────────────
    def dot_damage(self, hp_max, hp_cur):
        """本回合持续伤害总量（按气血上限 / 当前气血两类分别累加后取整）。

        ★ 先累加比例再乘，不要「每层各自取整再相加」：后者会让 1% 的
          极小额伤害在低气血时被每层都取整成 0，状态等于没生效。
        返回 (damage, 是否有 DOT)：没有任何 DOT 状态时返回 (0, False)。
        """
        pct_max = 0.0
        pct_cur = 0.0
        for eid in sorted(self._d):
            m = EFFECT_MODS.get(eid)
            if not m:
                continue
            n = max(1, int(self._d[eid].get('stacks') or 1))
            if m.get('dot_pct_max'):
                pct_max += float(m['dot_pct_max']) * n
            if m.get('dot_pct_cur'):
                pct_cur += float(m['dot_pct_cur']) * n
        if not pct_max and not pct_cur:
            return 0, False
        total = 0.0
        if pct_max:
            total += float(hp_max) * pct_max
        if pct_cur:
            total += float(hp_cur) * pct_cur
        # 至少 1 点：状态既然挂上了，就不该因为向下取整变成「零伤害挂着不掉」
        return max(1, int(total)), True

    # ── 序列化（存档 / GM 面板）────────────────────────────────
    def snapshot(self):
        return {str(k): dict(v) for k, v in self._d.items()}

    def restore(self, snap):
        """从 snapshot 恢复。未知 id / 坏数据一律丢弃（不炸存档）。"""
        self._d.clear()
        for k, v in (snap or {}).items():
            try:
                eid = int(k)
            except (TypeError, ValueError):
                continue
            if not is_known(eid):
                continue
            self._d[eid] = {
                'eid': eid,
                'turns': max(0, int(v.get('turns', 0))),
                'stacks': max(1, int(v.get('stacks', 1))),
                'source': v.get('source'),
            }
        return len(self._d)

    def summary(self):
        """一行摘要（日志/GM）。"""
        if not self._d:
            return '（无状态）'
        return '、'.join('%s×%d(%d回合)'
                        % (name_of(e), self._d[e]['stacks'], self._d[e]['turns'])
                        for e in sorted(self._d))


MAX_STACKS_HINT = MAX_STACKS    # 向后兼容别名（旧引用）


# ── 便捷包装（让调用方不必先建 EffectSet）──────────────────────────
def aggregate(sets):
    """把多个 EffectSet 的修正合并（如一队人共享的光环）。"""
    out = neutral()
    for es in sets:
        if es is None:
            continue
        m = es.mods()
        for k in _MUL_KEYS:
            out[k] *= m[k]
        for k in _ADD_KEYS:
            if k == 'hp_set':
                vals = [x for x in (out[k], m[k]) if x]
                out[k] = min(vals) if vals else 0
            else:
                out[k] += m[k]
        for k in _FLAG_KEYS:
            out[k] = out[k] or m[k]
    return out


def scaling(base, mul, minimum=0):
    """把倍率作用到基础值上并取整（伤害/属性统一走这里，避免各处取整口径不一）。"""
    return max(minimum, int(base * float(mul)))


def provenance():
    return {
        'module': 'combat_effects',
        'purpose': '状态效果机制（施加/到期/叠加/聚合）+ 数值转录',
        'levels': {
            'state_ids_and_text': 'TRUE（buff_data，客户端直读，284 条）',
            'numeric_params': 'PARTIAL（从说明文字转录，28 条，逐条带 src）',
            'unreadable': 'UNKNOWN（6 条有数字但无法量化 + 250 条无数字）',
            'stacking_rule': 'C（本服约定：乘区相乘、加法相加）',
        },
        'counts': {
            'known': len(bd.BUFF_INFO),                     # 284
            'quantified': len(bd.QUANT_CANDIDATES),         # 34（说明含 ASCII 数字）
            'modeled': len(EFFECT_MODS),                    # 29
            'unreadable': len(UNREADABLE),                  # 6（5 条有数字 + 25 号无数字）
            'no_number': len(bd.BUFF_INFO) - len(bd.QUANT_CANDIDATES),   # 250
        },
        'gaps': [
            '无状态参数表：强度与时长来自玩家可见说明文字，非二进制参数',
            '无施加方式/驱散条件/免疫判定的结构化数据',
            '修炼类加成（神力/坚盾/野性/冥界波）缺修炼表，只记层数不换算属性',
            '溺水 279 的「属性每回合递减」按剩余比例一次性作用，未做逐回合衰减',
        ],
    }
