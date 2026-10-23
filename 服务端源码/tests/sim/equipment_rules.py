# -*- coding: utf-8 -*-
u"""装备规则：穿哪个槽、够不够级、能不能穿（P3-01）。

只做**判定**，不改状态、不扣资源——调用方判定通过后才动背包与属性。
这样「非法操作不扣资源」是结构保证，而不是靠每个调用点自觉。

协议入口（真值，无新增 opcode）
--------------------------------
人物装备走 **`C2S_ITEM_DRAG(33)`**：`C2SItemDrag(id, source, dest)`，
`dest` ∈ 0..9 即装备位（`item.MAX_EQUIP_POS = 9`）。
`net.py` 的 138 条上行里**没有**独立的「装备/强化/修理」opcode（全量核对过），
所以本模块不新增任何上行，只在既有拖拽路径上加校验。

真值来源
--------
  OK 槽位语义 = 客户端 `itemdata.EQUIP2POS`
     （cap→1 weapon→2 cloth→3 sash→4 cuff→5 magic→6 necklace→7 ring→8 shoes→9），
     类型 id 区间 = `itemdata.ITEMTYPE`。两者都由 `gen_equip_data.py` 交叉对账后
     落进 `equip_data.py`（不一致会让生成脚本直接抛异常）。
  OK 等级门槛 = 物品描述里的 `【等级】N`（人物等级）。没有该字段的装备**不设等级门**。

证据缺口（必须如实标注，不猜）
------------------------------
  XX 装备属性（攻/防/速度/耐久）= **不可得**。客户端 `itemdata` 每条只有
     `(名称, 描述)` 两个字段，描述是文学文案（"剑薄如锋…【类型】武器：剑【等级】30"），
     **没有任何数值字段**；客户端也没有 `equipdata.pyo/py` 之类属性表（数据目录全量核对）。
     故 `EQUIP_STATS` 恒为空，装备不参与属性计算（见 `STAT_GAP`）。
  XX `【装备要求】50级剑法技巧` = **无法校验**。这是**技能**熟练度门槛，
     不是人物等级；本服没有剑法技巧/刀法技巧等熟练度体系（`skilldata` 里这些是
     客户端展示用，服务端无对应等级）。故只解析、只记录，不据此拦截（见 `SKILL_GAP`）。
  XX 耐久 / 修理：客户端文案确实写了「修理：支付一定的费用就可以使装备焕然一新
     （耐久度补满）」（`dialog/dlgmake.py` L232，按 GBK 字面量还原），
     但 (a) 物品没有耐久字段、(b) `C2S_NPC_MAKE(102)` 的子指令里无法确认哪一个是
     「修理」（客户端只暴露 `C2SMake(type,…)`，type 取值 0/1/2/3/6/7/8/11/12，
     没有 repair 命名）。**不猜子指令**，故未实现（见 `REPAIR_GAP`）。
"""
import equip_data

# ── 真值常量 ────────────────────────────────────────────────────────────
EQUIP_SLOT_MIN, EQUIP_SLOT_MAX = 1, 9      # 0 不是装备位（客户端 MAX_EQUIP_POS=9）
SLOT_NAMES = equip_data.SLOT_NAMES
EQUIP2POS = equip_data.EQUIP2POS

# ── 缺口声明（给 GM/文档/测试读，不是注释摆设）──────────────────────────
STAT_GAP = (u'装备攻防数值不可得：客户端 itemdata 只有名称+文学描述，无属性字段，'
            u'无 equipdata 表。不猜数值 → 装备不参与属性计算。')
SKILL_GAP = (u'【装备要求】是技能熟练度门槛（如「50级剑法技巧」），本服无该体系，'
             u'只解析记录、不拦截。')
REPAIR_GAP = (u'修理/耐久未实现：物品无耐久字段，且 C2S_NPC_MAKE(102) 的子指令里'
              u'无法确认哪一个是修理。不猜子指令。')

# 装备加成表：**故意为空**。有真值时在这里登记 shape → {字段: 值}，
# progression_service 会自动接上（它按 EQUIP_STATS 求和）。
EQUIP_STATS = {}

# 拒绝原因码（测试与日志共用一份，避免各处写字符串字面量）
R_OK = 'ok'
R_NOT_EQUIP = 'not_equipment'      # 不是装备，却往装备位拖
R_BAD_SLOT = 'bad_slot'            # 目标位不在 1..9
R_WRONG_SLOT = 'wrong_slot'        # 装备类型与槽位不符（头盔拖进鞋位）
R_LEVEL = 'level'                  # 人物等级不足


def info(shape):
    """shape → (名称, 类型, 槽位, 等级门, 装备要求, 类型原文)；非装备返回 None。"""
    return equip_data.EQUIP_INFO.get(int(shape))


def is_equipment(shape):
    return int(shape) in equip_data.EQUIP_INFO


def slot_of(shape):
    """该装备应去的槽位；非装备返回 None。"""
    row = info(shape)
    return row[2] if row else None


def level_gate(shape):
    """人物等级门槛；无门槛（或非装备）返回 None。"""
    row = info(shape)
    return row[3] if row else None


def skill_require(shape):
    """`【装备要求】` 原文（多为「50级剑法技巧」）；没有则 ''。"""
    row = info(shape)
    return row[4] if row else ''


def check_equip(shape, dest, grade):
    """能否把 `shape` 放进装备位 `dest`。

    :return: (ok, reason, detail)  —— 纯判定，无副作用
    """
    shape = int(shape)
    dest = int(dest)
    if not is_equipment(shape):
        return False, R_NOT_EQUIP, u'这不是装备，不能放进装备位'
    if not (EQUIP_SLOT_MIN <= dest <= EQUIP_SLOT_MAX):
        return False, R_BAD_SLOT, u'装备位只能是 %d..%d（0 是保留位）' \
            % (EQUIP_SLOT_MIN, EQUIP_SLOT_MAX)
    want = slot_of(shape)
    if dest != want:
        return False, R_WRONG_SLOT, u'%s 应放在「%s」位(%d)，不是 %d' % (
            info(shape)[0], SLOT_NAMES.get(want, '?'), want, dest)
    gate = level_gate(shape)
    if gate is not None and int(grade) < gate:
        return False, R_LEVEL, u'%s 需要 %d 级，当前 %d 级' % (
            info(shape)[0], gate, int(grade))
    return True, R_OK, u''


def describe(shape):
    """一行诊断文本（GM/调试面板用）。"""
    row = info(shape)
    if not row:
        return u'%d：不是装备' % int(shape)
    name, kind, slot, gate, require, kindtext = row
    return u'%d %s｜%s（%s）｜槽位 %d %s｜等级门 %s｜要求 %s' % (
        int(shape), name, kind, kindtext or '-', slot,
        SLOT_NAMES.get(slot, '?'), gate if gate is not None else '无',
        require or '无')
