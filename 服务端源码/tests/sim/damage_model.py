# -*- coding: utf-8 -*-
"""物伤公式（反向还原版）——纯函数，不含任何游戏状态。

来源
----
2026-10-19 反向还原的「基础物伤」原函数：

    有效防御 Deff = D × (1 − P/(30·Lt))
    Deff ≥ A  →  0.1A
    Deff < A  →  1.3 × (A − Deff)

参数（映射仍有待确认，见 _damage_feasible.py 的「未定项」）：
    A  = 攻击（候选 m_At）
    D  = 防御（候选 m_Df）
    P  = 破防（候选 m_Dp）或攻方等级（待判别）
    Lt = 等级（若 P 是破防，则 Lt 为守方等级）

设计约束
--------
1. **纯函数**：不读全局、不依赖 m4_server；同一个入参永远同一个出参。
2. **整数出参**：线上 `hp_chg` 是 `UnpackInt(4)`，客户端不接受小数；
   录像 50/48/20 三个值只有在「截断」语义下才能被 1.3×整数 精确复现
   （四舍五入在某些 gap 上无解，见 tests/_test_damage.py）。
3. **两种边界写法**：反向还原看到的是 if/else，但等价写法 `max(0.1A, 1.3(A−Deff))`
   只在「1.3(A−Deff) < 0.1A」的高防御区间不同；`mode` 参数把两者都保留下来，
   便于对拍时判别。现有 47 条录像样本全部落在正常分支，**判别不了**这两者。
4. **不做隐式 clamp**：P > 30·Lt 时 Deff < 0，伤害会超过 1.3A。是否截断属于
   原函数语义，调用方决定；这里如实返回，并在 docstring 里写明。

两个模型（`model` 参数）
------------------------
`reverse`（由 47 条录像反向还原）与 `linear`（外部受控变法实验表）**都能精确复现**
47 条录像真值，只是参数点不同：

| 模型 | 参数点 | 主角→怪 | 宠物→怪 | 怪→人 |
|---|---|---|---|---|
| `reverse` | hero_at=39 / hero_df=15 / pet_at=37 / enemy_at=31 | ⌊1.3×39⌋=50 | 48 | 20 |
| `linear`  | hero_at=100 / hero_df=60 / pet_at=96 / enemy_at=100 | (100−0)/2=50 | 48 | 20 |

`linear` 的来源是外部受控实验表（固定防御改攻击、固定攻击改防御，斜率稳定 0.5），
但该表的数值量级（攻击 690/防御 330）超出本作 TBYTE 上限，**未证实为本作数据**。

════════════════════════════════════════════════════════════════════════
★★ 两个模型的证据强度**不对等**，不要把「能算出 50/48/20」当成「还原了原服」★★
════════════════════════════════════════════════════════════════════════

  reverse —— **有出处**。反编译 mxmain.dll 得到的原函数形状：
             Deff = D×(1−P/(30·Lt));  Deff≥A ? 0.1A : 1.3×(A−Deff)
             这是「原服公式长什么样」的唯一实证线索。

  linear  —— **没有出处**。伤害 = (攻−防)/2，**唯一**依据是一份「外部受控变法实验表」：
             该表量级（攻击 690/防御 330）超出本作 TBYTE(0..255)；
             同批资料中的「撕天/威力293/效率28」在本作 petskill/magicdata 里查无此项；
             **原始出处文件不在本仓库**，无法复核。

  两者都能精确复现 47 条录像的 50/48/20 —— 也就是说，**光看伤害数字分不出谁对**。
  所以「默认用哪个」是个**工程取舍，不是证据结论**。

★ 两处「默认」不同，别混用（2026-10-19 校正）：
  · **本模块**裸调默认 `reverse`（`physical_damage(model='reverse')`）——反向还原的**参照点**；
  · **模拟服 m4_server** 实际默认 `linear`：`DMG_MODEL='linear'` + linear 参数点
    100/60/96/100，快照 ATTACK_DMG / PET_DAMAGE / ENEMY_ATTACK_DMG = 50 / 48 / 20。
    **选它做默认只是为了加减法直观、调参方便**，不代表本作真值；
    起服横幅会显式打一行「非真值 / 不代表已复刻原服公式」提醒。
  · 因此**切模型必须同时换参数点**：把 linear 的参数点配 reverse，伤害会差 2.6 倍
    （斜率 0.5 vs 1.3）。`world_config.dmg_model` 只切模型名、不换参数点，
    切之前先确认 battle 段的三点（hero_at / hero_df / pet_at / enemy_at）已同步。
"""
from __future__ import print_function

import random

GAIN = 1.3          # 正常分支系数
FLOOR_RATIO = 0.1   # 防御 ≥ 攻击时的保底比例
P_DIV = 30.0        # 破防稀释常数：P/(30·Lt)
LINEAR_DIV = 2.0    # 加减法模型的除数：伤害 = (攻−防)/2
MODELS = ('reverse', 'linear')


def effective_defense(defense, penetration=0, level=1, p_div=P_DIV):
    """有效防御 = D × (1 − P/(30·Lt))。

    level <= 0 时公式无定义（除零），抛 ValueError——不要静默返回 0，
    否则「等级没下发」会被伪装成「防御被完全击穿」。
    """
    if level <= 0:
        raise ValueError('level 必须 > 0（P/(30·Lt) 会除零）')
    return float(defense) * (1.0 - float(penetration) / (p_div * float(level)))


def _round(x, rounding):
    if rounding == 'trunc':
        return int(x)                       # 向零截断；伤害恒非负，等价 floor
    if rounding == 'floor':
        import math
        return int(math.floor(x))
    if rounding == 'round':
        return int(x + 0.5) if x >= 0 else -int(-x + 0.5)
    raise ValueError('未知 rounding=%r（trunc/floor/round）' % (rounding,))


def physical_damage(attack, defense, penetration=0, level=1,
                    gain=GAIN, floor_ratio=FLOOR_RATIO, p_div=P_DIV,
                    mode='if', rounding='trunc', model='reverse'):
    """基础物伤。

    model='reverse'：反向还原版（**本函数的默认**；有反编译出处，见模块头）
        Deff = D × (1 − P/(30·Lt))
        Deff ≥ A  → 0.1A            （保底）
        Deff < A  → 1.3 × (A − Deff)
    model='linear' ：加减法版（来自受控变法实验表，**来源不可考、别当复刻**；
                    模拟服选它做默认只为便利，见模块头与 m4_server 的参数段注释）
        伤害 = (攻 − 防) / 2，不使用 penetration / level；
        攻防差为负时按 0 处理——原表未覆盖负差区间，保底形状未知。

    rounding 默认 'trunc'（= int()）。所有分支都不会返回负数（A ≥ 0 时）。
    """
    if model not in MODELS:
        raise ValueError('未知 model=%r（%s）' % (model, '/'.join(MODELS)))
    if mode not in ('if', 'max'):
        raise ValueError("mode 只能是 'if' 或 'max'")
    atk = float(attack)
    if model == 'linear':
        return _round(max(0.0, (atk - float(defense)) / LINEAR_DIV), rounding)
    deff = effective_defense(defense, penetration, level, p_div)
    floor = floor_ratio * atk
    normal = gain * (atk - deff)
    if mode == 'if':
        return _round(floor if deff >= atk else normal, rounding)
    return _round(max(floor, normal), rounding)


def rng_factor(lo=1.0, hi=1.0, rnd=None):
    """伤害浮动系数，在 [lo, hi] 均匀取；lo == hi 时恒定（回归测试默认）。

    ★ 默认关浮动：47 条录像里同一来源的伤害**零方差**（23 次 50、13 次 48、
      11 次 20），若本作真有 ±5% 浮动，不可能一个 49/51 都不出现。
    """
    if hi < lo:
        lo, hi = hi, lo
    if hi <= lo:
        return lo
    rnd = rnd or random.random
    return lo + (hi - lo) * rnd()


def damage_gap(damage, gain=GAIN, rounding='trunc'):
    """反解：给定伤害，返回「A − Deff」的可行区间 [lo, hi)（连续）。

    满足 `_round(gain * g) == damage` 的全部 g。用于把录像真值翻译成参数约束。
    """
    if gain <= 0:
        raise ValueError('gain 必须 > 0')
    lo = float(damage) / gain
    hi = float(damage + 1) / gain
    if rounding == 'round':
        lo = (damage - 0.5) / gain
        hi = (damage + 0.5) / gain
    return lo, hi
