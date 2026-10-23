# -*- coding: utf-8 -*-
"""GM HTTP 与内核共享状态的并发安全。

背景：GM 面板（ThreadingHTTPServer，每请求一个线程）和调试台心跳（0.3~2s 一次）
与内核战斗线程同时跑。旧版 self._lock 只保护「取 _conn」这一步，
于是：
  · hero_hp / coins / items 等读写完全裸奔；
  · 更糟的是**发文动作在锁外** —— GM 的 WarStart 可能插在内核战斗帧中间。
    sendall 自身线程安全，但**协议帧序**不是：客户端按 opcode 顺序解析就错位。

实测（2026-10-20，加锁前）：GM 线程与内核线程并发写同一连接，帧序交错 8 次。

本测试断言：
  1. 锁是 RLock 且语义为「会话状态总锁」（gm_* 与 _dispatch 共用）
  2. 所有对外发文都发生在持锁状态下（包裹 sendall 检测）
  3. 高并发读写不抛异常、快照自洽（不会出现半更新组合）
  4. 内核 _dispatch 与 GM 写方法互斥：不出现帧序交错
"""
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


class LockRecorder(object):
    """RLock 代理：记录「当前是否处于临界区」，供 sendall 时查询。

    为什么不能直接问 RLock：它**可重入**，acquire(blocking=False) 在临界区内
    总是成功，release() 在计数 1 时也会成功（把计数降到 0）——两种探测法
    都会误判。用代理显式记账是唯一可靠的办法。
    """

    def __init__(self, rlock):
        self._lk = rlock
        self._depth = 0

    def __enter__(self):
        self._lk.acquire()
        self._depth += 1
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        self._lk.release()
        return False

    def acquire(self, blocking=True, timeout=-1):
        ok = self._lk.acquire(blocking, timeout)
        if ok:
            self._depth += 1
        return ok

    def release(self):
        self._depth -= 1
        return self._lk.release()

    def in_critical(self):
        return self._depth > 0


class RecConn(object):
    """记录每次 sendall 的（线程, 是否持锁, 帧头）。

    held 的含义：**发文瞬间是否处于临界区**（内核 _dispatch 或 gm_* 持锁）。
    """

    def __init__(self, srv):
        self.srv = srv
        self.buf = []

    def sendall(self, data):
        held = self.srv._lock.in_critical() \
            if hasattr(self.srv._lock, "in_critical") else False
        self.buf.append((threading.get_ident(), held, bytes(data[:2])))


def make_server(tmp, wrap_lock=True):
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                     logdir=os.path.join(tmp, 'logs'))
    srv.log = lambda *a, **k: None      # 静音，别让日志刷屏
    if wrap_lock:
        # 换成记账代理：只包一层，不改变锁语义
        srv._lock = LockRecorder(srv._lock)
    return srv


def main():
    tmp = tempfile.mkdtemp(prefix='gmlock_')
    try:
        # ── 1. 锁的类型与语义 ──
        #   先取未包装的：make_server 会换成记账代理，类型就看不出来了
        raw = m.M4Server('127.0.0.1', 0,
                         savedir=os.path.join(tmp, 'save'),
                         logdir=os.path.join(tmp, 'logs'))
        check('锁是 RLock（gm_* 会重入，普通 Lock 会自锁死）',
              isinstance(raw._lock, type(threading.RLock())),
              type(raw._lock).__name__)
        # 重入验证：同一条调用链上二次取锁不能死锁
        with raw._lock:
            with raw._lock:
                pass
        check('锁可重入（gm_* → _start_war 同链二次取锁不死锁）', True)
        srv = make_server(tmp)

        # ── 2. 所有发文都在持锁状态 ──
        conn = RecConn(srv)
        srv._conn = conn
        for i in range(50):
            srv.gm_set_hp(i * 7)
            srv.gm_set_coins(i * 100)
            srv.gm_add_item(22100, 1, 'GM')
            srv.gm_heal()
            srv.gm_refresh()
        unlocked = [b for b in conn.buf if not b[1]]
        check('GM 单线程发文全部持锁', not unlocked,
              '未持锁 %d 帧 / 共 %d' % (len(unlocked), len(conn.buf)))

        # ── 3. 并发读写：不抛异常、快照自洽 ──
        srv2 = make_server(tmp)
        c2 = RecConn(srv2)
        srv2._conn = c2
        for i in range(120):          # 先塞点东西，让 items 有实质工作量
            srv2.items[5000 + i] = {'shape': 22100, 'key': 0, 'qty': 1,
                                    'pos': 20, 'infoext': 'x' * 40}
        errors = []
        stop = threading.Event()

        def poller():
            while not stop.is_set():
                try:
                    st = srv2.gm_state_dict()
                    if st['coins'] < 0:
                        errors.append('coins 负数 %r' % st['coins'])
                    for it in st['items']:
                        if it['qty'] < 0 or it['shape'] < 0:
                            errors.append('item 字段异常 %r' % it)
                except Exception as e:            # noqa: BLE001
                    errors.append('读异常 %r' % (e,))
                    return

        def writer():
            for i in range(400):
                try:
                    srv2.gm_add_item(22100, 1, 'y')
                    srv2.gm_set_coins(i)
                    srv2.gm_set_hp(i % 400)
                    if i % 5 == 0:
                        srv2.gm_del_item(5000 + (i % 120))
                except Exception as e:            # noqa: BLE001
                    errors.append('写异常 %r' % (e,))
                    return

        polls = [threading.Thread(target=poller) for _ in range(4)]
        w = threading.Thread(target=writer)
        for t in polls:
            t.start()
        w.start()
        w.join()
        stop.set()
        for t in polls:
            t.join()
        check('并发读写 2000+ 次无异常、快照自洽', not errors,
              '%r' % (errors[:2],))
        unlocked2 = [b for b in c2.buf if not b[1]]
        check('并发发文全部持锁', not unlocked2,
              '未持锁 %d / %d' % (len(unlocked2), len(c2.buf)))

        # ── 4. GM 与内核互斥：发文不交错 ──
        #   内核侧必须自己持锁（_dispatch 已在锁内；这里模拟同一路径）
        srv3 = make_server(tmp)
        c3 = RecConn(srv3)
        srv3._conn = c3

        # ★ 轮次要够多：60 轮时样本太少，「内核不加锁」的变异也能碰巧 0 交错
        #   （实测变异后 60 轮 = 0 交错、300 轮 = 6 交错）。用 300 轮压。
        def gm_side():
            for i in range(300):
                srv3.gm_war_start()
                srv3.gm_war_win()

        def kernel_side():
            # ★ 不手动持锁：真实路径（handle 主循环）里的锁是**被测试代码自己**
            #   加的，这里再包一层会把「内核忘了加锁」这个缺陷掩盖掉。
            for i in range(300):
                srv3._dispatch(c3, m.C2S_WAR_ATTACK, bytes([1]))

        # ★ 交错次数受线程调度影响（同一变异 60 轮 0 次、300 轮 6 次），
        #   而「发文时锁被谁持有」也分不清（RLock 可重入、GM 侧长期持锁）。
        #   所以这里**先单线程验确定性不变式**，再并发验帧序。
        #
        # 确定性部分：没有任何 GM 线程干扰时，内核 _dispatch 必须自己持锁。
        # 把 with self._lock 变异掉 → 这些帧全部记为未持锁 → 稳定判红。
        # 先开战，否则 _dispatch(C2S_WAR_ATTACK) 不在战斗态、不会发文（0 帧 = 空断言）
        srv3.gm_war_start()
        c3.buf = []
        for i in range(20):
            srv3._dispatch(c3, m.C2S_WAR_ATTACK, bytes([1]))
        solo_unlocked = [b for b in c3.buf if not b[1]]
        check('内核 _dispatch 单独发文时持锁（确定性）',
              not solo_unlocked,
              '未持锁 %d / %d 帧' % (len(solo_unlocked), len(c3.buf)))

        # 并发部分：帧序不交错（概率性，但 300 轮下修复前实测 6 次）
        c3.buf = []
        ta = threading.Thread(target=gm_side)
        tb = threading.Thread(target=kernel_side)
        ta.start()
        tb.start()
        ta.join()
        tb.join()
        inter = sum(1 for a, b in zip(c3.buf, c3.buf[1:]) if a[0] != b[0])
        check('GM 与内核线程发文不交错', inter == 0,
              '交错 %d 次 / %d 帧' % (inter, len(c3.buf)))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('GM/内核并发锁：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  ✗ %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
