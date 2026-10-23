# -*- coding: utf-8 -*-
"""经济服务 —— 金钱/资源变动的**唯一入口**（P3-05）。

## 为什么需要它

`self.coins` 原先被 11 处代码直接 `+= / -=`。问题不是「写错」，而是**没有护栏**：

  · 扣费前不检查余额的写法随处可见 → 钱可以变负数
  · 同一次请求重发两次 → 扣两次
  · 同时扣多种资源（制作要 钱+活力+技能等级）时，可能扣了钱却没扣活力
  · 新加一条直接改 `self.coins` 的代码，没有任何机制会报警

本模块把「改钱」收成一个口子，并提供 `reconcile()` 对账：
账本推算值 ≠ 实际值时如实报出来 —— 那说明**有路径绕过了服务**。

## 与 `economy_ledger` 的分工

  · `economy_ledger` 只记账，不判断能不能做
  · `economy_service` 判断 + 改值 + 调账本 + 回调通知（发帧/发事件）
  · 数值出处（价格、奖励额）**不在这里**：那些在 `world_config.json`，
    且大量标注为模拟。本模块不臆造任何数值。

## 证据等级

机制 = **C（本服自建）**。原服的经济事务语义不可得（客户端无账单界面），
这里定义的规则是「本服约定」，不是复刻：
  · 余额不足 → 拒绝，不扣
  · 多资源扣费 → 全成功或全不动（原子）
  · 同一 `idem` 只生效一次
"""
from __future__ import division

import economy_ledger as el

R_OK = 'ok'
R_INSUFFICIENT = 'insufficient'      # 余额不足
R_DUPLICATE = 'duplicate'            # 同一幂等键已生效过
R_INVALID = 'invalid'                # 参数非法（负数/非整数/未知 kind）
R_LEDGER_REJECT = 'ledger_reject'    # 账本拒记（自相矛盾）→ 整笔回滚
R_NO_STATE = 'no_state'              # 没有可操作的状态对象

# 各种资源对应的状态属性名。新增资源在这里加一行即可。
KIND_ATTR = {
    el.KIND_COIN: 'coins',
    el.KIND_HUOLI: 'huoli',
    el.KIND_TILI: 'tili',
}


class Result(object):
    """一次经济操作的结果。调用方按 `ok` 分支，`message` 可直接给玩家看。"""

    __slots__ = ('ok', 'code', 'message', 'kind', 'delta', 'before', 'after',
                 'idem', 'parts')

    def __init__(self, ok, code, message, kind=None, delta=0, before=None,
                 after=None, idem=None, parts=None):
        self.ok = ok
        self.code = code
        self.message = message
        self.kind = kind
        self.delta = delta
        self.before = before
        self.after = after
        self.idem = idem
        self.parts = list(parts or [])

    def as_dict(self):
        return {
            'ok': self.ok, 'code': self.code, 'message': self.message,
            'kind': self.kind, 'delta': self.delta, 'before': self.before,
            'after': self.after, 'idem': self.idem,
            'parts': [p.as_dict() for p in self.parts],
        }

    def __repr__(self):
        return ('<Result %s %s %s %s→%s %s>'
                % ('OK' if self.ok else 'FAIL', self.code, self.kind,
                   self.before, self.after, self.message))


def _blank(ok, code, message, **kw):
    return Result(ok, code, message, **kw)


class EconomyService(object):
    """把「改钱」收成一个口子。

    owner       : 持有 `coins` / `huoli` / `tili` 属性的对象（通常是 M4Server）
    ledger      : 账本；None 时自建一个
    on_change   : 回调 (kind, before, after, delta, reason) -> None
                  服务端在这里发 PropChange 帧 + 发总线事件
    log         : 日志回调（可选）
    """

    def __init__(self, owner=None, ledger=None, on_change=None, log=None):
        self.owner = owner
        self.ledger = ledger if ledger is not None else el.EconomyLedger()
        self.on_change = on_change
        self.log = log

    # ── 内部：取/设值 ────────────────────────────────────────────────
    def _attr(self, kind):
        return KIND_ATTR.get(kind)

    def value(self, kind=el.KIND_COIN):
        """当前值；不可用返回 None。"""
        name = self._attr(kind)
        if self.owner is None or not name:
            return None
        v = getattr(self.owner, name, None)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    def _set(self, kind, value):
        setattr(self.owner, self._attr(kind), int(value))

    # ── 单资源 ──────────────────────────────────────────────────────
    def charge(self, amount, reason, idem=None, kind=el.KIND_COIN):
        """扣 amount（正数）。余额不足 → 不动。"""
        return self.charge_multi([(kind, amount)], reason, idem=idem)

    def grant(self, amount, reason, idem=None, kind=el.KIND_COIN):
        """加 amount（正数）。"""
        return self.grant_multi([(kind, amount)], reason, idem=idem)

    # ── 多资源（原子）────────────────────────────────────────────────
    def charge_multi(self, parts, reason, idem=None):
        """一次扣多种资源：**全成功或全不动**。

        parts: [(kind, amount), ...]，amount 必须是正整数。

        为什么必须原子：制作一次要扣 钱+活力+技能等级。若先扣了钱、
        再发现活力不够，玩家就白亏了钱 —— 而这类「扣了一半」的 bug
        在没有原子保证时极难发现（余额看起来只是个偏小的数）。
        """
        norm, err = self._normalize(parts)
        if err:
            return err
        if self.owner is None:
            return _blank(False, R_NO_STATE, '没有经济状态对象')

        # 幂等：任意一个 kind 的键用过，就整体视为重发（不做事）。
        if idem is not None and any(
                self.ledger.has_idem(self._idem_for(idem, k)) for k, _ in norm):
            return _blank(False, R_DUPLICATE,
                          '重复提交（%s），未重复扣费' % idem, idem=idem)

        # ① 先全量检查
        before = {}
        for kind, amt in norm:
            cur = self.value(kind)
            if cur is None:
                return _blank(False, R_INVALID, '资源 %s 不可用' % kind, kind=kind)
            if cur < amt:
                return _blank(
                    False, R_INSUFFICIENT,
                    '%s不足：需要 %d，现有 %d' % (
                        el.KIND_LABEL.get(kind, kind), amt, cur),
                    kind=kind, delta=-amt, before=cur, after=cur, idem=idem)
            before[kind] = cur

        # ② 记账（先记账后改值：账本若拒记，此时还没动过任何值）
        results = []
        for kind, amt in norm:
            b = before[kind]
            a = b - amt
            e = self.ledger.record(kind, -amt, b, a, reason,
                                   idem=self._idem_for(idem, kind))
            if e is None:
                # 回滚：把已经记过的账删掉不现实（账本只追加），
                # 但值还没改，所以只需报告「整笔未生效」。
                return _blank(False, R_LEDGER_REJECT,
                              '账本拒记（%s -%d），本笔未生效' % (kind, amt),
                              kind=kind, delta=-amt, before=b, after=b,
                              idem=idem)
            results.append(_blank(True, R_OK, reason, kind=kind, delta=-amt,
                                  before=b, after=a, idem=idem))

        # ③ 改值 + 通知
        for kind, amt in norm:
            b = before[kind]
            self._set(kind, b - amt)
            self._notify(kind, b, b - amt, -amt, reason)

        out = results[0] if len(results) == 1 else _blank(
            True, R_OK, reason, idem=idem, parts=results)
        out.parts = results if len(results) > 1 else []
        return out

    def grant_multi(self, parts, reason, idem=None):
        """一次给多种资源。没有「不够」的问题，所以只需原子记账。"""
        norm, err = self._normalize(parts, allow_positive_only=True)
        if err:
            return err
        if self.owner is None:
            return _blank(False, R_NO_STATE, '没有经济状态对象')
        if idem is not None and any(
                self.ledger.has_idem(self._idem_for(idem, k)) for k, _ in norm):
            return _blank(False, R_DUPLICATE,
                          '重复提交（%s），未重复发放' % idem, idem=idem)

        # ① 先全量取当前值（此时还没记账、没改值）
        before = {}
        for kind, amt in norm:
            b = self.value(kind)
            if b is None:
                return _blank(False, R_INVALID, '资源 %s 不可用' % kind, kind=kind)
            before[kind] = b

        # ② 全量记账 —— 与 charge_multi 同样的三段式。
        #    ★ 2026-10-23 改：原先是「记一笔→改一个值」交替，第二笔被拒时
        #      第一笔已经生效了，等于对调用方撒谎（docstring 写的是全成功或全不动）。
        results = []
        for kind, amt in norm:
            b = before[kind]
            a = b + amt
            e = self.ledger.record(kind, amt, b, a, reason,
                                   idem=self._idem_for(idem, kind))
            if e is None:
                return _blank(False, R_LEDGER_REJECT,
                              '账本拒记（%s +%d），本笔未生效' % (kind, amt),
                              kind=kind, delta=amt, before=b, after=b, idem=idem)
            results.append(_blank(True, R_OK, reason, kind=kind, delta=amt,
                                  before=b, after=a, idem=idem))

        # ③ 全量改值 + 通知
        for kind, amt in norm:
            b = before[kind]
            self._set(kind, b + amt)
            self._notify(kind, b, b + amt, amt, reason)

        out = results[0] if len(results) == 1 else _blank(
            True, R_OK, reason, idem=idem, parts=results)
        out.parts = results if len(results) > 1 else []
        return out

    # ── 绝对值（GM 用）──────────────────────────────────────────────
    def set_absolute(self, value, reason, idem=None, kind=el.KIND_COIN):
        """把资源设为指定值。GM 改钱走这里，同样留流水。"""
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return _blank(False, R_INVALID, '目标值必须是非负整数：%r' % (value,))
        if self.owner is None:
            return _blank(False, R_NO_STATE, '没有经济状态对象')
        if idem is not None and self.ledger.has_idem(self._idem_for(idem, kind)):
            return _blank(False, R_DUPLICATE,
                          '重复提交（%s），未重复设置' % idem, idem=idem)
        b = self.value(kind)
        if b is None:
            return _blank(False, R_INVALID, '资源 %s 不可用' % kind, kind=kind)
        d = value - b
        if d == 0:
            return _blank(True, R_OK, '无变化', kind=kind, delta=0,
                          before=b, after=b, idem=idem)
        e = self.ledger.record(kind, d, b, value, reason,
                               idem=self._idem_for(idem, kind))
        if e is None:
            return _blank(False, R_LEDGER_REJECT, '账本拒记，未改动',
                          kind=kind, delta=d, before=b, after=b, idem=idem)
        self._set(kind, value)
        self._notify(kind, b, value, d, reason)
        return _blank(True, R_OK, reason, kind=kind, delta=d,
                      before=b, after=value, idem=idem)

    # ── 对账 / 观测 ─────────────────────────────────────────────────
    def reconcile(self, kind=el.KIND_COIN):
        """账本推算值 vs 实际值。返回 (ok, detail)；ok=None 表示无法判定。"""
        actual = self.value(kind)
        if actual is None:
            return None, '资源 %s 不可用' % kind
        return self.ledger.reconcile(kind, actual)

    def stats(self):
        return self.ledger.stats()

    def tail(self, n=20, kind=None):
        return self.ledger.tail(n, kind)

    # ── 内部 ────────────────────────────────────────────────────────
    @staticmethod
    def _idem_for(idem, kind):
        """多资源用同一个 idem 时，按 kind 派生独立键。

        否则「制作扣 钱+活力」只会记进第一笔，活力那一笔会被判成重发 ——
        这正是账本用全局 idem 集合时会踩的坑。
        """
        if idem is None:
            return None
        return '%s|%s' % (idem, kind)

    def _normalize(self, parts, allow_positive_only=True):
        """校验 parts，返回 ([(kind, amount)], Result|None)。"""
        if not parts:
            return [], _blank(False, R_INVALID, '没有要变动的资源')
        out = []
        seen = set()
        for item in parts:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                return [], _blank(False, R_INVALID, '资源项格式应为 (kind, amount)')
            kind, amt = item
            if kind not in KIND_ATTR:
                return [], _blank(False, R_INVALID, '未知资源类型：%r' % (kind,))
            if not isinstance(amt, int) or isinstance(amt, bool):
                return [], _blank(False, R_INVALID,
                                  '数量必须是整数：%r' % (amt,))
            if amt <= 0:
                return [], _blank(False, R_INVALID,
                                  '数量必须为正（方向由 charge/grant 决定）：%d' % amt)
            if kind in seen:
                return [], _blank(False, R_INVALID,
                                  '同一笔里重复指定资源：%s' % kind)
            seen.add(kind)
            out.append((kind, amt))
        return out, None

    def _notify(self, kind, before, after, delta, reason):
        if self.on_change is None:
            return
        try:
            self.on_change(kind, before, after, delta, reason)
        except Exception:                            # noqa: BLE001
            # 通知失败不能影响钱已经改对这件事；日志留着就够。
            if self.log is not None:
                try:
                    self.log('   ⚠ 经济变更通知失败（%s %+d）' % (kind, delta))
                except Exception:                    # noqa: BLE001
                    pass
