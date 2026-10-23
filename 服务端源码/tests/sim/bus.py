# -*- coding: utf-8 -*-
"""业务事件总线（Business Event Bus）—— 把「发生了什么业务」从「发了什么帧」里分离出来。

为什么需要它
------------
调试台原本只有**帧流**这一层视角：看得见 `ItemAdd(33)`、`PropChange(17)`，
看不见「为什么发」——是任务奖励？是买东西找零？是制作扣费？
帧是**表现**，业务是**因果**。只盯帧流的后果是：

  · 想查「金币怎么少了 200」，要在几十条 PropChange 里人工对齐下标与数值；
  · 想查「任务卡在第几步」，得去翻 tasks 字典再对照 quest_data 的步骤表；
  · 拒绝路径（钱不够 / 活力不够 / 数量不足）**一帧都不发**，帧流里完全不存在，
    于是「点了没反应」这类问题在调试台上是隐形的。

业务事件总线补的就是这一层：内核在**业务判定点**上发语义事件，
调试台据此画出「任务链」与「经济」两个页签。

设计约束
--------
1. **只观测、不驱动**：事件是单向广播，内核不读总线的返回值。
   业务判定一律在内核里做完了才发事件 —— 总线挂了/没订阅者不能影响玩法。
2. **异常安全**：`emit()` 吞掉订阅者抛出的所有异常。调试用的东西不能把服务器搞崩。
   这是硬要求：订阅者是调试台/测试代码，它们的 bug 不该让客户端掉线。
3. **有界**：环形缓冲（默认 2000 条），超了淘汰最老的并**计数**（不静默丢）。
   与 tap 的 `_evicted` 同一套原则：宁可显示「丢了 N 条」，也不假装完整。
4. **线程安全**：内核线程、GM HTTP 线程、调试台心跳线程会并发 emit / 读。
   订阅回调在**锁外**调用（否则订阅者里再 emit 就死锁）。

事件记录格式（JSON 友好，直接喂给前端）::

    {'seq': 1, 't': 1761..., 'cat': 'quest', 'kind': 'quest.step',
     'msg': '主线 102 推进到第 3 步', 'qid': 102, 'step': 3, ...}

`cat` 是页签级的粗分类（quest/econ/item/war/craft/world），`kind` 是细分类。
前端按 cat 分栏、按 kind 着色。

用法::

    import bus
    bus.emit('quest.step', qid=102, step=3, msg='...')     # 进程级默认总线
    bus.BUS.subscribe(fn)                                  # 订阅
    bus.BUS.since(0, cats=['quest'])                       # 拉取

仅使用标准库。
"""
from __future__ import print_function

import threading
import time
from collections import deque

# ── 事件分类：`cat` 决定进哪个页签 ──────────────────────────────────────────
CAT_QUEST = 'quest'     # 主线剧情：接取/推进/完成/放弃/剧情战斗
CAT_ECON = 'econ'       # 经济：金币与经验的收支
CAT_ITEM = 'item'       # 物品：发放 / 扣除（背包层面）
CAT_WAR = 'war'         # 战斗：开始 / 结束（胜/败/逃）
CAT_CRAFT = 'craft'     # 工艺：制作成功 / 各类拒绝
CAT_WORLD = 'world'     # 世界：切图 / 传送

CATS = (CAT_QUEST, CAT_ECON, CAT_ITEM, CAT_WAR, CAT_CRAFT, CAT_WORLD)

CAT_LABEL = {
    CAT_QUEST: '任务链',
    CAT_ECON: '经济',
    CAT_ITEM: '物品',
    CAT_WAR: '战斗',
    CAT_CRAFT: '工艺',
    CAT_WORLD: '世界',
}

# ── kind → (cat, 默认文案) ──────────────────────────────────────────────────
# 登记表的意义：kind 写错（拼错 / 用了没登记的新 kind）在 `stats()` 里会被归到
# `unregistered`，而不是悄悄混进某个页签。这是「别让新代码偷偷造分类」的守卫。
KIND_CAT = {
    # 任务链
    'quest.accept':    (CAT_QUEST, '接取主线'),
    'quest.step':      (CAT_QUEST, '步骤推进'),
    'quest.complete':  (CAT_QUEST, '主线完成'),
    'quest.giveup':    (CAT_QUEST, '放弃主线'),
    'quest.reject':    (CAT_QUEST, '交互被拒'),
    'quest.reward':    (CAT_QUEST, '剧情奖励'),
    'quest.point':     (CAT_QUEST, '剧情点变动'),
    # 经济
    'econ.coins':      (CAT_ECON, '金币变动'),
    'econ.exp':        (CAT_ECON, '经验变动'),
    'econ.levelup':    (CAT_ECON, '升级'),
    # 物品
    'item.grant':      (CAT_ITEM, '获得物品'),
    'item.take':       (CAT_ITEM, '扣除物品'),
    # 战斗
    'war.start':       (CAT_WAR, '进入战斗'),
    'war.end':         (CAT_WAR, '战斗结束'),
    # 工艺
    'craft.ok':        (CAT_CRAFT, '制作成功'),
    'craft.reject':    (CAT_CRAFT, '制作被拒'),
    # 世界
    'world.goto':      (CAT_WORLD, '切图'),
}

# 金额类事件的字段约定（经济页签据此画流水）：
#   delta  变动量（正=收入，负=支出）
#   after  变动后余额
#   reason 人类可读的原因
ECON_KINDS = ('econ.coins', 'econ.exp')


class EventBus(object):
    """有界环形缓冲 + 订阅广播的业务事件总线。"""

    def __init__(self, capacity=2000):
        self._cap = int(capacity)
        self._lock = threading.RLock()
        self._subs = []              # [callable(rec)]，回调在锁外调用
        self._ring = deque(maxlen=self._cap)
        self._seq = 0                # 已发出的最大 seq（从 1 开始）
        self._counts = {}            # kind -> 次数
        self._dropped = 0            # 环形缓冲淘汰计数
        self._unregistered = {}      # 未登记的 kind -> 次数

    # ── 写 ──
    def emit(self, kind, msg=None, **fields):
        """发一条业务事件，返回记录（未登记 kind 也会记录，但会被统计标记）。

        订阅者抛异常一律吞掉 —— 见模块头「设计约束 2」。
        """
        cat = None
        with self._lock:
            self._seq += 1
            info = KIND_CAT.get(kind)
            if info is None:
                cat = fields.pop('cat', None) or 'other'
                self._unregistered[kind] = self._unregistered.get(kind, 0) + 1
                text = msg or kind
            else:
                cat, label = info
                text = msg or label
            rec = {'seq': self._seq, 't': round(time.time(), 3),
                   'cat': cat, 'kind': kind, 'msg': text}
            rec.update(fields)
            if len(self._ring) == self._cap:
                self._dropped += 1
            self._ring.append(rec)
            self._counts[kind] = self._counts.get(kind, 0) + 1
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(rec)
            except Exception:                          # noqa: BLE001
                pass
        return rec

    def subscribe(self, fn):
        """订阅；返回反订阅函数（幂等）。"""
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

        def _off():
            with self._lock:
                try:
                    self._subs.remove(fn)
                except ValueError:
                    pass
        return _off

    # ── 读 ──
    def since(self, seq=0, cats=None, kinds=None, limit=None):
        """取 seq 之后的事件（不含该 seq）。cats/kinds 为空表示不过滤。"""
        cs = set(cats) if cats else None
        ks = set(kinds) if kinds else None
        with self._lock:
            out = [r for r in self._ring
                   if r['seq'] > seq
                   and (cs is None or r['cat'] in cs)
                   and (ks is None or r['kind'] in ks)]
        if limit and len(out) > limit:
            out = out[-int(limit):]
        return out

    def stats(self):
        with self._lock:
            by_cat = {}
            for r in self._ring:
                by_cat[r['cat']] = by_cat.get(r['cat'], 0) + 1
            return {
                'seq': self._seq,
                'total': self._seq,
                'buffered': len(self._ring),
                'capacity': self._cap,
                'dropped': self._dropped,
                'counts': dict(self._counts),
                'by_cat': by_cat,
                'unregistered': dict(self._unregistered),
                'subscribers': len(self._subs),
            }

    def reset(self):
        """清空（测试用：每个用例一个干净总线，否则 seq 会跨用例串）。"""
        with self._lock:
            self._ring.clear()
            self._seq = 0
            self._counts.clear()
            self._dropped = 0
            self._unregistered.clear()


# ── 进程级默认总线 ──────────────────────────────────────────────────────────
# 内核是单例（单机模式只允许一个客户端），进程级总线与之同构。
# 测试要隔离就用 `swap_bus()` 换一个干净的。
BUS = EventBus()


def emit(kind, msg=None, **fields):
    return BUS.emit(kind, msg=msg, **fields)


def reset():
    BUS.reset()


def swap_bus(new_bus=None):
    """换掉默认总线（测试隔离用），返回旧总线以便还原。"""
    global BUS
    old = BUS
    BUS = new_bus if new_bus is not None else EventBus()
    return old


# ── 经济流水：`econ.coins` / `econ.exp` 的汇总视图 ──────────────────────────
def ledger(kinds=ECON_KINDS, limit=200):
    """把金额类事件翻成流水行（经济页签的表体）。

    只认带 `delta` 的事件；`after` 缺失时留空（前端显示 —）。
    """
    rows = BUS.since(0, kinds=list(kinds))
    out = []
    for r in rows:
        out.append({'seq': r['seq'], 't': r['t'], 'kind': r['kind'],
                    'delta': r.get('delta'), 'after': r.get('after'),
                    'reason': r.get('reason') or r['msg']})
    if limit and len(out) > limit:
        out = out[-int(limit):]
    return out


def ledger_totals(kinds=ECON_KINDS):
    """按 kind 汇总收支（收入 / 支出 / 净额）。"""
    rows = BUS.since(0, kinds=list(kinds))
    out = {}
    for r in rows:
        d = r.get('delta')
        if not isinstance(d, (int, float)):
            continue
        agg = out.setdefault(r['kind'], {'in': 0, 'out': 0, 'net': 0, 'n': 0})
        if d >= 0:
            agg['in'] += d
        else:
            agg['out'] += -d
        agg['net'] += d
        agg['n'] += 1
    return out
