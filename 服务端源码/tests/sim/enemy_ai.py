# -*- coding: utf-8 -*-
"""敌方 AI 决策 —— P3-03。

## 这个模块解决什么

P3-03 之前，「敌方这一回合打谁、放不放招、放哪招、会不会心」四处判断直接写在
`m4_server._enemy_attack()` 里，混在 40 行帧编排中间：

    tgt = random.choice(targets)                      # 打谁
    use_skill = random.random() < self.enemy_skill_chance   # 放不放
    pid = random.choice(self.enemy_skills)            # 放哪招
    hurt = HURT_BS if random.random() < self._hurt_bs_chance else HURT_NONE

四个问题，三个后果：

  · **不可解释** —— 「这只怪为什么一直打宠物」只能回答「随机」，说不出理由；
  · **不可替换** —— 想换一种打法（优先打残血、优先打法系）必须改战斗代码，
    而那段代码同时负责发帧，改错就是帧序崩；
  · **不可断言** —— 决策与协议揉在一起，测试只能看最终发了什么帧，
    分不清「决策错了」还是「帧拼错了」。

本模块把**决策**抽成纯函数：输入（谁活着、保护关系、概率配置、随机源），
输出一个决策字典。不碰协议、不碰 `M4Server` 状态、不发帧。

## 证据等级

  · 「原服敌方如何选目标」**不可得**（UNKNOWN）。本模块的每条策略都是
    **SIM**（本服模拟），`DefaultPolicy` 的职责是**逐位复刻既有行为**，
    不是「还原原服 AI」。
  · 概率值（`enemy_skill_chance` / `_hurt_bs_chance`）沿用既有常量，
    证据等级不因本模块改变。

## 不变式（有测试锁）

  I1 `DefaultPolicy` 的随机抽取**种类与顺序**与原实现一致：
     target → skill_chance → skill_pick → hurt，一样不多、一样不少。
  I2 决策是纯函数：同样的输入 + 同样的 rng 状态 → 同样的输出；
     且 `pick_target` 在 FOCUS 策略下**不消耗**随机数。
  I3 `protected=True` 时**不放技能**（保护必用普攻表现，见原实现注释）。
  I4 决策永远返回一个存活目标；目标列表为空时返回 `None`（调用方兜）。
"""
from __future__ import division

import battle_rng as brng


# 策略名（GM 可切换；默认 'random' = 既有行为）
P_RANDOM = 'random'
P_FOCUS = 'focus'
POLICIES = (P_RANDOM, P_FOCUS)

POLICY_TITLE = {
    P_RANDOM: '随机（复刻既有行为）',
    P_FOCUS: '集火残血（优先打气血比例最低的友方）',
}


def new_decision():
    """空决策（字段固定，便于逐键断言 —— 不要用 dict.get 到处兜）。"""
    return {
        'target': None,      # 最终承受攻击的 war id（保护生效时是保护者）
        'intended': None,    # 原本被选中的 war id（保护前）
        'protected': False,  # 是否触发了保护转移
        'use_skill': False,  # 是否用招式表现（True 时 skill_id 必非空）
        'skill_id': None,
        'crit': False,       # 是否红字（HURT_BS）
    }


def pick_target(rng, targets, policy=P_RANDOM, hp_ratio=None):
    """选本回合的攻击目标（原意目标，**未**考虑保护转移）。

    拆成独立函数而不是塞进 `decide()` 的原因：保护关系要先知道「打谁」才能查
    （`_round_protectors.pop(tgt)`），而「是否放技能」又依赖「有没有发生保护」。
    顺序天然是 选目标 → 查保护 → 决定动作，硬塞进一个函数只能变成回调或重复抽取 ——
    后者会让随机消耗翻倍，同种子复现直接失效。

    `targets` 为空时返回 None。
    """
    targets = list(targets or [])
    if not targets:
        return None
    if policy == P_FOCUS and hp_ratio:
        # 集火：气血比例最低者优先；并列时取 targets 里靠前的（同序稳定）。
        # ★ 不消耗随机数 —— 策略本身是确定性的；这里若掷一次骰，
        #   后续所有流的序列都会跟着漂，「集火」这个只是换个目标的策略
        #   会顺手改掉会心/掉落的运气，排查时极其难解释。
        order = list(targets)
        return min(order, key=lambda w: (hp_ratio.get(w, 1.0), order.index(w)))
    return rng.choice(brng.S_ENEMY_TARGET, targets)


def decide_action(rng, skill_chance, skills, hurt_chance, protected=False):
    """决定「放不放招 / 放哪招 / 会不会心」。返回决策字典（不含 target 字段）。

    抽取顺序（与原实现一致，见 I1）：
        ① 是否放招（先掷骰）
        ② 保护否决（不掷骰，只在已掷出 True 时把结果改成 False）
        ③ 放哪招（仅在确实要放时抽）
        ④ 会心（无论如何都掷一次）
    """
    dec = new_decision()

    # ① 是否放招
    dec['use_skill'] = rng.chance(brng.S_ENEMY_SKILL, skill_chance)

    # ② 保护否决
    if protected:
        # 客户端只在普通攻击命令里查找保护者，所以保护触发时必须用普攻表现
        dec['protected'] = True
        dec['use_skill'] = False

    # ③ 放哪招
    if dec['use_skill']:
        dec['skill_id'] = rng.choice(brng.S_ENEMY_PICK, skills)
        if dec['skill_id'] is None:      # 招式表为空 → 退回普攻
            dec['use_skill'] = False

    # ④ 会心
    dec['crit'] = rng.chance(brng.S_ENEMY_HURT, hurt_chance)
    return dec


def decide(rng, targets, skill_chance, skills, hurt_chance, policy=P_RANDOM,
           hp_ratio=None, protector=None):
    """一步到位的便利封装（无保护关系的简单场景用）。

    `protector` 传一个可调用对象 `f(target) -> 保护者 war id 或 None`，
    内部会在选完目标后调用它；不方便传回调时请改用
    `pick_target()` + `decide_action()` 两步式。
    """
    tgt = pick_target(rng, targets, policy=policy, hp_ratio=hp_ratio)
    if tgt is None:
        return None
    prot = None
    if protector is not None:
        prot = protector(tgt)
    ok = prot is not None and prot in list(targets)
    dec = decide_action(rng, skill_chance, skills, hurt_chance, protected=ok)
    dec['intended'] = tgt
    dec['target'] = prot if ok else tgt
    return dec


def explain(dec, names=None):
    """把决策翻成一句人话（日志/GM 面板）。

    `names` 可选 {war_id: 显示名}；缺省时直接用 id。
    """
    if not dec:
        return '无目标'
    def nm(w):
        return (names or {}).get(w, str(w))
    parts = ['打 %s' % nm(dec['target'])]
    if dec['protected']:
        parts.append('（保护转移：原定 %s）' % nm(dec['intended']))
    if dec['use_skill'] and dec['skill_id'] is not None:
        parts.append('放招式 %d' % dec['skill_id'])
    else:
        parts.append('普攻')
    if dec['crit']:
        parts.append('会心')
    return ' '.join(parts)


def provenance():
    return {
        'module': 'enemy_ai',
        'purpose': '敌方行动决策（选目标 / 放不放招 / 选招 / 会心）',
        'level': 'SIM',
        'notes': [
            '原服敌方 AI 不可得（UNKNOWN）；本模块是**本服模拟策略**',
            'DefaultPolicy 的职责是逐位复刻既有行为，不是还原原服',
            '概率常量沿用 m4_server 既有值，证据等级不变',
        ],
        'policies': list(POLICIES),
        'invariants': ['I1 抽取顺序一致', 'I2 纯函数', 'I3 保护不放招', 'I4 必有目标'],
    }
