# -*- coding: utf-8 -*-
"""战斗随机源：固定种子 + **流分离**（P3-03）。

## 为什么要有这个模块

P3-03 之前，战斗里的随机全部直接调模块级 `random.*`（共 12 处，见 §收口点）。
那意味着：

  · **不可复现** —— 同一场战斗重放一次，连击/会心/掉落/敌方选目标全都不一样，
    「刚才那次为什么赢」无法回答，录像对不上也没法判断是公式错还是运气差。
  · **互相污染** —— 全局流是一条线：多触发一次掉落判定，就会把后面「敌方打谁」
    的随机数挤走。于是「关掉掉落」这样一个无关改动，会让敌方行为整体换一套。
  · **无法注入** —— 想测「连击必触发」只能改业务代码或全局 `random.seed()`，
    后者会污染同进程里的其它测试。

本模块把随机收口成**实例级**对象，并按用途切成独立流：

    encounter  遇怪选怪          combo  宠物连击
    crit       宠物会心          enemy_hurt  敌方会心（红字）
    drop       掉落掷骰          drop_pick   掉落池/条目挑选
    enemy_skill  敌方是否放招式  enemy_pick 敌方招式挑选
    enemy_target 敌方选目标      escape      逃跑判定
    misc       其它

同一条流内的抽取顺序固定 → **同种子 + 同操作序列 ⇒ 逐位相同的结果**；
流与流之间互不影响 → 关掉掉落不会改变敌方打谁。

## 证据等级

  · 「按用途分多条流」是 **SIM**（本服工程决定，原服实现不可见）。
  · 「同种子可复现」是 Python `random.Random` 的性质，属 **C**（本服约定）。
  · 各流**概率值**（`DROP_CHANCE` / `ENEMY_SKILL_CHANCE` …）沿用既有口径，
    其证据等级不因本模块改变。

## 不变式（有测试锁）

  I1 同种子 → 同场次 → 同一串抽取（逐位相等）
  I2 不同流互不影响（抽乱 drop 流，enemy_target 流的下一个值不变）
  I3 `begin_battle()` 之后，每一场都从「本场种子」重新起算
  I4 `enabled=False` 时退化为直通模块级 `random`（仅供兼容旧脚本）
"""
from __future__ import division

import random
import zlib


# ── 流名（集中登记，避免各处散写字符串拼错）──────────────────────────
S_ENCOUNTER = 'encounter'
S_COMBO = 'combo'
S_CRIT = 'crit'
S_ENEMY_HURT = 'enemy_hurt'
S_DROP = 'drop'
S_DROP_PICK = 'drop_pick'
S_ENEMY_SKILL = 'enemy_skill'
S_ENEMY_PICK = 'enemy_pick'
S_ENEMY_TARGET = 'enemy_target'
S_ESCAPE = 'escape'
S_MISC = 'misc'

STREAMS = (S_ENCOUNTER, S_COMBO, S_CRIT, S_ENEMY_HURT, S_DROP, S_DROP_PICK,
           S_ENEMY_SKILL, S_ENEMY_PICK, S_ENEMY_TARGET, S_ESCAPE, S_MISC)

# 场次混合常数（0x9E3779B1 = 黄金比 2^32/φ，取奇数避免与低比特对齐）
_BATTLE_MIX = 0x9E3779B1
_MASK32 = 0xFFFFFFFF


def _mix32(*vals):
    """把若干整数混成一个 32 位种子。

    不用内置 `hash()`：字符串 hash 在 Python 3 里带进程级随机化，
    同一流名在不同进程会派生出不同种子 —— 那样「跨进程可复现」就没了。
    """
    h = 0x811C9DC5                     # FNV-1a offset basis
    for v in vals:
        v = int(v) & _MASK32
        for _ in range(4):
            h ^= v & 0xFF
            h = (h * 0x01000193) & _MASK32   # FNV prime
            v >>= 8
    return h


def _tag_seed(name):
    """流名的稳定 32 位摘要（与进程无关）。"""
    return zlib.crc32(name.encode('utf-8')) & _MASK32


class BattleRng(object):
    """战斗随机源。一个 `M4Server` 实例持有一个。

    典型用法::

        self.battle_rng.begin_battle()                 # 开战：重置本场
        tgt = self.battle_rng.choice(S_ENEMY_TARGET, targets)
        if self.battle_rng.chance(S_CRIT, 0.2): ...
    """

    def __init__(self, seed=0, enabled=True):
        self._seed = int(seed) & _MASK32
        self._enabled = bool(enabled)
        self._battle = 0                    # 场次计数（begin_battle 递增）
        self._streams = {}                  # 流名 → random.Random
        self._draws = {}                    # 流名 → 抽取次数（调试/断言用）
        self._battle_seed = self._seed

    # ── 生命周期 ──────────────────────────────────────────────
    @property
    def enabled(self):
        """True=固定种子流；False=直通模块级 random（I4）。"""
        return self._enabled

    @enabled.setter
    def enabled(self, v):
        v = bool(v)
        if v == self._enabled:
            return
        self._enabled = v
        # 切换后旧流作废：一边用独立流抽了一半、一边又用全局流，
        # 状态对不上会得到「既不是 A 也不是 B」的第三套序列。
        self._streams.clear()
        self._draws.clear()

    def begin_battle(self, salt=None):
        """开战：按「主种子 + 场次」派生本场种子，并清空所有流。

        每场独立起算，所以「第 3 场的第 1 次掉落」与「第 1 场」互不牵连 ——
        否则掉过一次落后，后面所有场的随机序列都会被那次历史挤位。
        """
        self._battle += 1
        salt = 0 if salt is None else int(salt)
        self._battle_seed = _mix32(self._seed, self._battle * _BATTLE_MIX, salt)
        self._streams.clear()
        self._draws.clear()
        return self._battle_seed

    def reset(self, seed=None):
        """彻底重置（含场次归零）。测试用。"""
        if seed is not None:
            self._seed = int(seed) & _MASK32
        self._battle = 0
        self._battle_seed = self._seed
        self._streams.clear()
        self._draws.clear()

    # ── 内部：取某条流 ────────────────────────────────────────
    def _stream(self, name):
        if not self._enabled:
            return random                       # 模块级直通（I4）
        r = self._streams.get(name)
        if r is None:
            if name not in STREAMS:             # 拼错流名要当场报，不能静默新建
                raise KeyError('未知随机流 %r（已登记：%s）'
                               % (name, ', '.join(STREAMS)))
            r = random.Random(_mix32(self._battle_seed, _tag_seed(name)))
            self._streams[name] = r
            self._draws[name] = 0
        return r

    def _count(self, name, n=1):
        if self._enabled and name in self._draws:
            self._draws[name] += n

    # ── 抽取 API（全部带流名）─────────────────────────────────
    def roll(self, name):
        """[0,1) 均匀值。"""
        v = self._stream(name).random()
        self._count(name)
        return v

    def chance(self, name, p):
        """伯努利判定：`random() < p`。p<=0 恒 False，p>=1 恒 True（**不消耗**随机数）。"""
        try:
            p = float(p)
        except (TypeError, ValueError):
            p = 0.0
        if p <= 0.0:
            return False
        if p >= 1.0:
            return True
        return self.roll(name) < p

    def choice(self, name, seq):
        """从序列里等概率取一个。空序列 → None（不抛，调用方自己兜）。"""
        seq = list(seq)
        if not seq:
            return None
        if len(seq) == 1:
            # 单元素不消耗随机数：否则「场上只剩一个目标」这种必然事件
            # 也会推动随机流，让后续序列无故漂移。
            return seq[0]
        v = self._stream(name).randrange(len(seq))
        self._count(name)
        return seq[v]

    def randint(self, name, a, b):
        """闭区间整数。"""
        a, b = int(a), int(b)
        if a == b:
            return a
        v = self._stream(name).randrange(a, b + 1)
        self._count(name)
        return v

    def shuffled(self, name, seq):
        """返回打乱后的新列表（不改入参）。"""
        out = list(seq)
        r = self._stream(name)
        r.shuffle(out)
        self._count(name, max(0, len(out) - 1))
        return out

    # ── 观测 ──────────────────────────────────────────────────
    def draws(self, name=None):
        """某条流（或全部）已抽取次数 —— 测试用来断言「这条路径确实掷了骰子」。"""
        if name is None:
            return dict(self._draws)
        return self._draws.get(name, 0)

    def state(self):
        """当前随机状态的快照（便于测试/调试复现）。"""
        return {
            'seed': self._seed,
            'battle': self._battle,
            'battle_seed': self._battle_seed,
            'enabled': self._enabled,
            'draws': dict(self._draws),
            'streams': {k: v.getstate() for k, v in self._streams.items()},
        }

    def restore(self, snap):
        """从 `state()` 的快照恢复（含各流内部状态）。"""
        self._seed = int(snap.get('seed', 0)) & _MASK32
        self._battle = int(snap.get('battle', 0))
        self._battle_seed = int(snap.get('battle_seed', self._seed)) & _MASK32
        self._enabled = bool(snap.get('enabled', True))
        self._draws = dict(snap.get('draws') or {})
        self._streams = {}
        for k, st in (snap.get('streams') or {}).items():
            r = random.Random(0)
            r.setstate(st)
            self._streams[k] = r

    def summary(self):
        """一行摘要（日志/GM 面板用）。"""
        return ('rng seed=0x%08X battle=%d 本场=0x%08X 抽取=%d 次'
                % (self._seed, self._battle, self._battle_seed,
                   sum(self._draws.values())))


def provenance():
    """证据来源（供 `_p4_audit` / 文档引用）。"""
    return {
        'module': 'battle_rng',
        'purpose': '战斗随机源收口：固定种子 + 流分离',
        'level': 'SIM',
        'notes': [
            '「按用途分多条流」是本服工程决定（SIM），原服随机实现不可见',
            '「同种子可复现」是 random.Random 的确定性性质（C）',
            '各流概率值沿用既有常量，证据等级不因本模块改变',
        ],
        'invariants': ['I1 同种子同序', 'I2 流间隔离',
                       'I3 每场重置', 'I4 可退化为直通'],
    }
