# -*- coding: utf-8 -*-
u"""宠物属性：**唯一计算入口**（P3-02）。

三层分工（沿用既有 0..65535 面板层 / 0..255 战斗层约定）
-------------------------------------------------------
  · 面板层  `panel_stats()`   = petfightdata 公式（真值）+ 五维加点（SIM）
      喂 S2CSummonInfo(48) 的 m_Damage / m_Defense / m_Speed / m_MagicDef。
  · 战斗层  `combat_stats()`  = 录像标定值 × 真值**相对倍率** → 0..255
      喂 `_dmg()`。为什么不把面板值直接丢进伤害公式：reverse 物伤模型是在
      (AT=37 / DF=15 / DP=4 / SP=49) 这组**录像读数**上拟合的，面板档喂进去
      会大两个数量级（同一只 1 级企鹅面板 at≈578）。
  · 资源层  `resource_max()`  = 气血/魔法按真值倍率，力道恒为录像值
      （`PET_FP` 没有任何面板来源，`mg` 公式是「法攻」不是「蓝量」，
       拿它当 MP 语义就错了——2026-10-21 踩过这个坑）。

为什么必须收成一个入口
----------------------
和 P3-01 的 `progression_service` 同一条理由：**派生值只算不存**。
宠物属性是 (等级, 资质, 五维, 加成) 的纯函数结果，算 100 遍必须一样；
一旦允许某处 `p['stats']['at'] += ...` 式累加，「重登重放」「换宠再切回」
「升级重算」三条路径立刻叠加，而且叠加后无法反推哪一份是脏的。

真值 / 模拟 / 缺口
------------------
  A 真值  资质的成长公式、`exp_need` 升级门槛 —— 客户端 `petfightdata.py`
          原件（`battle_formulas.calc_pet_stats` / `pet_exp_need`）。
          本模块**不重算公式**，只做「公式结果 + 加点」的合成，避免抄第二份。
  A 真值  五维**出厂值** 10/10/10/10/10 与加点字节序（Con 在前的 5 字节）
          —— 录像面板 + `dlgpet._id2attr`。
  S 模拟  `POINT_EFFECT`：五维 → 面板数值的换算（力量→伤害 +2/点 …）。
          客户端只显示五维数字，真正的换算在服务端，本库无数据。
  S 模拟  `POINT_PER_LEVEL`（每级 5 点）/ `EXP_SHARE`（出战拿几成经验）。
  G 缺口  召唤兽装备的**数值**：`itemdata` 无属性字段，装备目前只影响
          「穿了什么」的展示（op54），对属性贡献恒为 0。见 `EQUIP_GAP`。
"""
import equipment_rules as eqr

# ── 资质下标（3..8 = hp/at/df/mg/md/sp，与 petfightdata.PET_ATTR_IDX 同源）──
ATTR_IDXS = (3, 4, 5, 6, 7, 8)
ATTR_NAME = {3: 'hp', 4: 'at', 5: 'df', 6: 'mg', 7: 'md', 8: 'sp'}

LEVEL_MAX = 120
POINT_PER_LEVEL = 5          # ★ SIM：每升 1 级给 5 点自由点（无真值）
EXP_SHARE = 1.0              # ★ SIM：出战宠物拿战斗经验的比例（原服比例不可得）
SLOT_MAX = 8                 # 携带上限（SIM；资料只说「受人物等级限制」）

# 加点字节顺序 **Con 在前**：`dlgpet.py` 的 `_id2attr = {8:0, 23:1, 22:2, 20:3, 19:4}`
# 与 `PET_POINTS` 的 (m_Con, m_Str, m_Mag, m_Cha, m_Dex) 对齐 —— 先 Con 后 Str。
# 按「Str 先」解会把玩家加在体质上的点记成力量（面板立刻对不上）。
POINT_ORDER = ('con', 'str', 'mag', 'cha', 'dex')

# 五维 → 面板数值换算（★ SIM：效果在服务端，无公式可依）
POINT_EFFECT = {
    'str': ('at', 2),      # 力量 → 伤害 +2/点
    'con': ('hp', 10),     # 体质 → 气血上限 +10/点
    'mag': ('md', 2),      # 法力 → 法防 +2/点
    'cha': ('df', 1),      # 魅力 → 防御 +1/点
    'dex': ('sp', 1),      # 敏捷 → 速度 +1/点
}

# 出厂五维（录像面板值）。这 10 点**不算**加点加成，只有超出部分才算 ——
# 否则「默认宠物 = 录像基准 ×1.0」立刻破（面板凭空多 +100 血 / +20 攻）。
DEFAULT_ATTR = 10
POINT_BASE = {k: DEFAULT_ATTR for k in POINT_ORDER}

# 缺口文案（写进 GM 面板与文档，避免口径漂移）
EQUIP_GAP = u'召唤兽装备数值不可得（itemdata 无属性字段）→ 对属性贡献恒为 0'
TALENT_GAP = u'资质洗练/调整无客户端入口（dlgpet 只有加点与查看）→ 未实现'
SKILL_SRC_GAP = u'魔兽要诀 → 技能 的映射不可得（itemdata 只有单一道具 25405）'


def _get(attrs, key, default):
    """从五维 dict 取一维；缺失/脏值一律回落出厂值（旧档、手改档都安全）。"""
    try:
        return int((attrs or {}).get(key, default))
    except (TypeError, ValueError):
        return int(default)


def point_bonus(attrs, base=None, effect=None):
    """五维加点 → {面板属性: 加成}。**只算超出出厂值的部分**。

    返回的 dict 只含**非零**项，方便直接拼人类可读的属性来源说明；
    调用方做合并时不必再判空。
    """
    base = POINT_BASE if base is None else base
    effect = POINT_EFFECT if effect is None else effect
    out = {}
    for key, spec in effect.items():
        try:
            target, per = spec[0], int(spec[1])
        except (IndexError, TypeError, ValueError):
            continue
        if not per:
            continue
        add = max(0, _get(attrs, key, base.get(key, DEFAULT_ATTR))
                  - int(base.get(key, DEFAULT_ATTR)))
        if add:
            out[target] = out.get(target, 0) + per * add
    return out


def panel_stats(stats, attrs, base=None, effect=None):
    """面板层：公式结果 + 加点加成 → 新 dict（**不改入参**）。

    :param stats: `calc_pet_stats()` 的结果（{'hp','at','df','mg','md','sp',…}）
    :param attrs: 五维 {'con','str','mag','cha','dex'}
    :return: 合并后的 dict；`stats` 里没有的属性 key 不会被凭空造出来
    """
    out = dict(stats or {})
    for target, add in point_bonus(attrs, base, effect).items():
        if target in out:
            out[target] = int(out[target]) + int(add)
    return out


def ratio(stats, ref, key, default=1.0):
    """相对倍率 = 实例真值 ÷ 参考宠真值。参考宠（默认新宠）恒为 ×1.0。

    分母/分子任一缺失或 ≤0 → 回落 `default`（1.0）。宁可「按录像基准算」，
    也不要因为一只没算过属性的宠物把伤害打成 0。
    """
    try:
        r = float((ref or {}).get(key) or 0)
        c = float((stats or {}).get(key) or 0)
    except (TypeError, ValueError):
        return default
    if r <= 0 or c <= 0:
        return default
    return c / r


# 战斗层/资源层要的键 → 面板层 stats 里的键（`magdef` 取自资质 `md`）
COMBAT_KEY_MAP = {'at': 'at', 'df': 'df', 'speed': 'sp', 'magdef': 'md'}
RES_KEY_MAP = {'hp': 'hp', 'mp': 'mg'}


def combat_ratios(stats, ref):
    """战斗层倍率 {'at','df','speed','magdef'}。"""
    return {ck: ratio(stats, ref, sk) for ck, sk in COMBAT_KEY_MAP.items()}


def resource_ratios(stats, ref):
    """资源层倍率 {'hp','mp'}。"""
    return {ck: ratio(stats, ref, sk) for ck, sk in RES_KEY_MAP.items()}


def combat_stats(base, r):
    """战斗层：录像标定值 × 倍率。

    `base` 是录像标定值（AT=37 / DF=15 / DP=4 / SP=49 / MAGDEF=36）。
    ⚠ 破防（dp）**没有面板来源**，恒取标定值 —— 别给它编倍率。
    """
    base = dict(base or {})
    r = dict(r or {})
    return {
        'at': max(1, int(round(int(base.get('at', 0)) * float(r.get('at', 1.0))))),
        'df': max(0, int(round(int(base.get('df', 0)) * float(r.get('df', 1.0))))),
        'dp': int(base.get('dp', 0)),
        'speed': max(1, int(round(int(base.get('speed', 0))
                                  * float(r.get('speed', 1.0))))),
        'magdef': max(0, int(round(int(base.get('magdef', 0))
                                   * float(r.get('magdef', 1.0))))),
    }


def resource_max(base, r):
    """资源层上限 {hp, mp, fp}。

    气血/魔法按真值倍率缩放；**力道恒定**（`PET_FP` 无面板来源）。
    """
    base = dict(base or {})
    r = dict(r or {})
    return {
        'hp': max(1, int(round(int(base.get('hp', 0)) * float(r.get('hp', 1.0))))),
        'mp': max(0, int(round(int(base.get('mp', 0)) * float(r.get('mp', 1.0))))),
        'fp': max(0, int(base.get('fp', 0))),
    }


def normalize_attr_lv(level, attr_lv=None, idxs=ATTR_IDXS):
    """资质等级归一：每项 = max(存档值, 宠物等级)，**只增不减**。

    签名与语义沿用 `recompute_pet_display()` 的既有 SIM 约定（资质等级跟随宠物
    等级）。返回**全新 dict 且键为 int** —— 存档 JSON 会把键变成字符串 '3'，
    读档路径虽已转回 int，这里再收一次口，避免出现 {'3':.., 3:..} 双份。

    只增不减的好处：GM/存档里手工调高的资质不会被一次 normalize 压回去，
    同时「反复调用」是幂等的（max 的不动点）。
    """
    attr = dict(attr_lv or {})
    lv = max(1, int(level or 1))
    out = {}
    for i in idxs:
        # 两种键都要认：内存里是 int，存档 JSON 里是 '3'。只认 int 的话，
        # 手改档里调高的资质会被静默压回等级值（「只增不减」当场失效）。
        raw = attr.get(i, attr.get(str(i), 1))
        try:
            cur = int(raw or 1)
        except (TypeError, ValueError):
            cur = 1
        out[int(i)] = max(cur, lv)
    return out


def ability(base, stats, ref):
    """面板「资质」五项（Ability_Attack/Defense/Con/Magic/Speed）。

    录像给的 (1797,1792,1880,1828,1909) 是**默认宠物**的读数；其他宠物/等级按
    真值倍率同比放大。
    ⚠ SIM：这五项在原服是**资质评级**（不随等级线性变化），这里只保证
      「更强的宠物资质评级更高、默认宠物与录像逐位一致」。
    """
    out = []
    for v, sk in zip(tuple(base or ()), ('at', 'df', 'hp', 'mg', 'sp')):
        try:
            out.append(max(0, min(65535, int(round(int(v) * ratio(stats, ref, sk))))))
        except (TypeError, ValueError):
            out.append(0)
    return tuple(out)


def explain_lines(pid, level, base_panel, panel, combat_base, combat,
                  res, stat_ratios=None, name=''):
    """人类可读的属性来源说明（GM `petstats` 用）。

    `base_panel` 是**不含加点**的公式结果，`panel` 是合并后的结果 ——
    两个一减就是「玩家加点贡献了多少」，这正是配置错乱时最想看到的一行。
    """
    lines = [u'宠物 #%s %s（%s 级）' % (pid, name or '?', level)]
    bonus = {}
    for k in ('hp', 'at', 'df', 'mg', 'md', 'sp'):
        b = int((base_panel or {}).get(k, 0) or 0)
        f = int((panel or {}).get(k, 0) or 0)
        bonus[k] = f - b
    lines.append(u'  面板层（真值公式 + 加点）')
    for k in ('hp', 'at', 'df', 'mg', 'md', 'sp'):
        b = int((base_panel or {}).get(k, 0) or 0)
        f = int((panel or {}).get(k, 0) or 0)
        lines.append(u'    %-3s = %-6d  (公式 %d, 加点 %+d)' % (k, f, b, bonus[k]))
    # ★ 面板上真会显示的气血/魔法不是这两个数：`S2CSummonInfo` 的 m_HP/m_MP
    #   取的是资源层（录像值 × 倍率），而资质 `hp`/`mg` 只是公式产物。
    #   两个数并排出现时不说清楚，排查的人一定会以为哪里算错了。
    lines.append(u'    └ hp/mg 是**资质基值**；面板的 m_HP/m_MP 走下面的资源层')
    lines.append(u'  战斗层（录像标定 × 真值倍率）')
    for k in ('at', 'df', 'dp', 'speed', 'magdef'):
        b = int((combat_base or {}).get(k, 0) or 0)
        f = int((combat or {}).get(k, 0) or 0)
        r = (stat_ratios or {}).get(k)
        lines.append(u'    %-6s = %-4d  (标定 %d%s)'
                     % (k, f, b, '' if r is None else u', ×%.3f' % r))
    lines.append(u'  资源层：HP %s / MP %s / FP %s'
                 % (res.get('hp'), res.get('mp'), res.get('fp')))
    return lines


def provenance():
    """真值/SIM/缺口一览（写进文档与 GM 面板，避免口径漂移）。"""
    return {
        'panel': 'A 真值（petfightdata 公式，battle_formulas 原件）+ SIM 加点换算',
        'point_effect': 'SIM（五维→面板，服务端在算，本库无公式）',
        'point_per_level': 'SIM（每级 %d 点）' % POINT_PER_LEVEL,
        'exp_share': 'SIM（出战拿 %.0f%% 战斗经验）' % (EXP_SHARE * 100),
        'combat': 'C 约定（录像标定值 × 真值相对倍率；标定值见 PET_*_COMBAT）',
        'resource_fp': 'A 真值（录像 30；无面板来源，故不缩放）',
        'equip': 'GAP（%s）' % EQUIP_GAP,
        'talent': 'GAP（%s）' % TALENT_GAP,
        'skill_source': 'GAP（%s）' % SKILL_SRC_GAP,
        'equip_rules': 'GAP（%s）' % eqr.STAT_GAP,
    }
