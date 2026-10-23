# -*- coding: utf-8 -*-
"""经济流水账本 —— P3-05。

## 为什么需要它

P3-05 之前，本服的钱只有一处真相：`self.coins` 这个整数。它被 **11 处代码**
直接 `+= / -=`（商店买卖、制作扣费、任务奖励、GM 改钱…）。这带来三个问题：

1. **无法回答「钱是怎么变成现在这个数的」** —— 没有流水，只有结果。
2. **无法防重复提交** —— 同一个请求重发两次，钱就扣两次。玩家看到的是
   「卡了一下，钱少了两份」。
3. **无法发现绕过** —— 新加一条直接改 `self.coins` 的代码，没有任何机制会报警。

本模块只做**记账**：每笔金钱/物品变动留一条不可变的记录，带前后值、原因、
可选的幂等键。它不碰业务规则（能不能买、够不够钱由 `economy_service` 判）。

## 证据等级

**机制 = 本服自建（C 级）**。原服的经济流水格式不可得，客户端也没有「账单」界面。
所以这里不声称复刻原服，只保证：本服每一笔钱都能倒查。

价格/掉率/奖励的**数值**另有出处，见 `world_config.json` 的 `provenance` 段
（大量标注为「模拟｜真值待补」）—— 本模块不碰数值，只记变化。

## 设计要点

· **只记整数**：钱是整数，任何浮点进入账本都是 bug（直接拒绝）。
· **前后值必须自洽**：`before + delta == after`，否则拒绝写入 —— 账本记错比不记更糟。
· **幂等键可选**：给了就必须唯一；重复的键**不记账、只返回 False**，让调用方知道
  这是重发而不是新交易。
· **有界**：默认只保留最近 `MAX_ENTRIES` 条（战斗/商店高频，长时间跑会吃内存）。
  淘汰的是最旧的明细，但**累计统计不丢**（`stats()` 用累加器而不是遍历明细）。
· **不落盘**：流水是观测设施，不是存档的一部分。要带走用 `to_list()` 导出。
"""
from __future__ import division

# 账本保留的明细条数上限。超出后丢最旧的一条。
# 取值理由：一次商店/制作/任务各 1 条，正常一小时几十条；4096 足够覆盖
# 一次长时间 soak，又不至于让诊断包过大。
MAX_ENTRIES = 4096

# 变动类型（kind）。新增类型要同步 `KIND_LABEL`，否则 GM 面板显示裸键名。
KIND_COIN = 'coin'          # 金钱
KIND_ITEM = 'item'          # 物品增减（delta 是数量，sign 表示进出）
KIND_HUOLI = 'huoli'        # 活力
KIND_TILI = 'tili'          # 体力

KIND_LABEL = {
    KIND_COIN: '金钱',
    KIND_ITEM: '物品',
    KIND_HUOLI: '活力',
    KIND_TILI: '体力',
}


class LedgerEntry(object):
    """一笔变动。字段只读（用 property 而不是直接暴露可写属性）。"""

    __slots__ = ('_seq', '_kind', '_delta', '_before', '_after',
                 '_reason', '_idem', '_extra')

    def __init__(self, seq, kind, delta, before, after, reason, idem=None,
                 extra=None):
        self._seq = seq
        self._kind = kind
        self._delta = delta
        self._before = before
        self._after = after
        self._reason = reason
        self._idem = idem
        self._extra = dict(extra or {})

    @property
    def seq(self):
        return self._seq

    @property
    def kind(self):
        return self._kind

    @property
    def delta(self):
        return self._delta

    @property
    def before(self):
        return self._before

    @property
    def after(self):
        return self._after

    @property
    def reason(self):
        return self._reason

    @property
    def idem(self):
        return self._idem

    @property
    def extra(self):
        return dict(self._extra)

    def as_dict(self):
        return {
            'seq': self._seq,
            'kind': self._kind,
            'kind_label': KIND_LABEL.get(self._kind, self._kind),
            'delta': self._delta,
            'before': self._before,
            'after': self._after,
            'reason': self._reason,
            'idem': self._idem,
            'extra': dict(self._extra),
        }

    def __repr__(self):
        return ('<LedgerEntry #%d %s %+d %d→%d %s%s>'
                % (self._seq, self._kind, self._delta, self._before,
                   self._after, self._reason,
                   (' idem=%s' % self._idem) if self._idem else ''))


class EconomyLedger(object):
    """有界、可对账、带幂等键的变动流水。"""

    def __init__(self, max_entries=MAX_ENTRIES):
        self.max_entries = max(1, int(max_entries))
        self._seq = 0
        self._entries = []          # list[LedgerEntry]，按 seq 递增
        self._idem = set()          # 已用过的幂等键
        self._dropped = 0           # 被淘汰的明细条数（统计用，不影响累计）
        # 累计统计：按 kind 分别累计「进」「出」的绝对值与笔数。
        # 为什么要累加器而不是遍历明细：明细会被淘汰，遍历会算漏。
        self._acc = {}
        # 对账基线：账本第一次记账时记下当时的真实值，用于 reconcile。
        self._baseline = {}

    # ── 记账 ────────────────────────────────────────────────────────
    def record(self, kind, delta, before, after, reason, idem=None, extra=None):
        """记一笔。返回 LedgerEntry；被拒时返回 None（原因见返回值语义）。

        拒绝条件（一律返回 None，**不抛异常**：记账失败不该让玩法崩）：
          · delta 不是 int（浮点/字符串）
          · before / after 不是 int
          · before + delta != after（账本自相矛盾）
          · delta == 0（无变化不记账，否则流水会被空转刷满）
          · idem 已用过（重发）
        """
        if not isinstance(delta, int) or isinstance(delta, bool):
            return None
        if not isinstance(before, int) or isinstance(before, bool):
            return None
        if not isinstance(after, int) or isinstance(after, bool):
            return None
        if delta == 0:
            return None
        if before + delta != after:
            return None
        if idem is not None:
            if idem in self._idem:
                return None
            self._idem.add(idem)

        self._seq += 1
        e = LedgerEntry(self._seq, kind, delta, before, after, reason, idem, extra)
        self._entries.append(e)

        a = self._acc.setdefault(kind, {'in': 0, 'out': 0, 'n_in': 0, 'n_out': 0})
        if delta > 0:
            a['in'] += delta
            a['n_in'] += 1
        else:
            a['out'] += -delta
            a['n_out'] += 1

        self._baseline.setdefault(kind, before)

        while len(self._entries) > self.max_entries:
            self._entries.pop(0)
            self._dropped += 1
        return e

    # ── 查询 ────────────────────────────────────────────────────────
    def has_idem(self, idem):
        return idem is not None and idem in self._idem

    def tail(self, n=20, kind=None):
        """最近 n 条（新的在前）。"""
        out = []
        for e in reversed(self._entries):
            if kind is not None and e.kind != kind:
                continue
            out.append(e)
            if len(out) >= n:
                break
        return out

    def entries(self):
        return list(self._entries)

    def stats(self):
        """按 kind 汇总：进/出总额、笔数、净额、基础值/当前值、被淘汰条数。"""
        out = {'dropped': self._dropped, 'total': self._seq, 'kinds': {}}
        for kind, a in self._acc.items():
            last = self._last_after(kind)
            out['kinds'][kind] = {
                'label': KIND_LABEL.get(kind, kind),
                'in': a['in'], 'out': a['out'],
                'net': a['in'] - a['out'],
                'n_in': a['n_in'], 'n_out': a['n_out'],
                'baseline': self._baseline.get(kind),
                'current': last,
            }
        return out

    def _last_after(self, kind):
        for e in reversed(self._entries):
            if e.kind == kind:
                return e.after
        return None

    def reconcile(self, kind, actual, expect_baseline=None):
        """对账：账本推算值 vs 实际值。

        推算值 = 基准值 + 明细净额。明细被淘汰过就**无法推算**（返回 None），
        因为丢掉的那部分差额无从补回 —— 这时候要说「不可对账」，不能假装对上。

        返回 (ok, detail)。ok=None 表示无法判定。
        """
        if self._dropped > 0:
            return None, '明细被淘汰 %d 条，无法用明细对账' % self._dropped
        base = self._baseline.get(kind) if expect_baseline is None else expect_baseline
        if base is None:
            return None, '没有 %s 的记账基线（本轮还没记过账）' % kind
        delta = 0
        for e in self._entries:
            if e.kind == kind:
                delta += e.delta
        calc = base + delta
        return (calc == actual), '%s 账本推算 %d，实际 %d，差 %d' % (
            KIND_LABEL.get(kind, kind), calc, actual, actual - calc)

    def to_list(self, limit=None):
        """导出成纯 dict 列表（给诊断包/JSON）。新的在前，便于看最近的。"""
        rows = [e.as_dict() for e in reversed(self._entries)]
        if limit is not None:
            return rows[:max(0, int(limit))]
        return rows

    def reset(self):
        self._seq = 0
        self._entries = []
        self._idem = set()
        self._dropped = 0
        self._acc = {}
        self._baseline = {}
