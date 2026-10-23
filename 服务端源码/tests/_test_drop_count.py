# -*- coding: utf-8 -*-
"""丢帧计数：环形缓冲淘汰 + SSE 批推溢出。

背景：调试台有两处会**静默丢帧**，此前都没有计数：
  1. tap.FrameTap._push —— 环形缓冲超过 ring_max 时删最老的帧；
  2. debugger.Broadcaster._on_frame —— 批推暂存超过 2000 条时删最老的。
两处丢了都不留痕，界面上看着正常，实际「看到的不是全部历史」。

与 _dropped 的区别（别混）：
  _dropped    调试台**主动**丢帧（drop_next，为了测客户端缺帧容错）
  _evicted    环形缓冲**被动**淘汰（长期历史）
  _sse_dropped 批推暂存**被动**淘汰（两次推送之间的积压）

本测试断言三者独立、计数准确、clear 会归零。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import tap as T  # noqa: E402
import debugger as D  # noqa: E402

class _DummyConn(object):
    """TappedConn 的底层替身：只吞字节，不做任何事。"""
    def sendall(self, data):
        return None

    def settimeout(self, t):
        pass

    def gettimeout(self):
        return 0.1

    def close(self):
        pass


results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def main():
    # ── 1. 环形缓冲淘汰计数 ──
    t = T.FrameTap(ring_max=5)
    for i in range(12):
        t._record('C2S', 8, b'')
    st = t.stats(include_tables=False)
    check('环形缓冲淘汰计数准确（12 帧 / ring=5 → 7 帧淘汰）',
          st['evicted'] == 7, 'evicted=%r' % (st['evicted'],))
    check('ring_full 标志为真', st['ring_full'] is True)
    check('缓冲内实际保留 ring_max 帧', len(t._ring) == 5,
          'len=%d' % len(t._ring))

    # ── 2. 未溢出时不计数 ──
    t2 = T.FrameTap(ring_max=100)
    for i in range(10):
        t2._record('C2S', 8, b'')
    st2 = t2.stats(include_tables=False)
    check('未溢出时 evicted=0 且 ring_full=False',
          st2['evicted'] == 0 and st2['ring_full'] is False,
          'evicted=%r ring_full=%r' % (st2['evicted'], st2['ring_full']))

    # ── 3. 主动丢帧与被动淘汰是两个独立计数 ──
    #   注意：主动丢帧必须走 TappedConn._filter_drop 的真实路径，
    #   直接调 _record 不会触发丢弃（首版就这么测错了，dropped 恒为 0）。
    t3 = T.FrameTap(ring_max=5)
    t3.drop_next('S2C')                    # 布一次「丢下一帧」
    conn = T.TappedConn(_DummyConn(), t3)
    wire = T.make_s2c(152, b'hello')
    conn.sendall(wire)                     # 这一帧会被 _filter_drop 吞掉
    for i in range(10):                    # 再灌 10 帧撑爆环形缓冲
        conn.sendall(T.make_s2c(152, b'x'))
    st3 = t3.stats(include_tables=False)
    check('主动丢帧计入 dropped', st3['dropped']['S2C'] == 1,
          'dropped=%r' % (st3['dropped'],))
    check('被动淘汰计入 evicted（与 dropped 独立）', st3['evicted'] > 0,
          'evicted=%r' % (st3['evicted'],))
    check('两个计数互不覆盖',
          st3['dropped']['S2C'] == 1 and st3['evicted'] != 1,
          'dropped=%d evicted=%d' % (st3['dropped']['S2C'], st3['evicted']))

    # ── 4. SSE 批推溢出计数 ──
    b = D.Broadcaster(T.FrameTap())
    for i in range(2100):
        b._on_frame({'seq': i})
    check('批推溢出计数（2100 帧 / 上限 2000 → 100 帧溢出）',
          b._sse_dropped == 100, 'sse_dropped=%r' % (b._sse_dropped,))
    check('批推暂存实际保留 2000 条', len(b._buf) == 2000,
          'len=%d' % len(b._buf))

    # ── 5. clear() 归零 ──
    t.clear()
    stc = t.stats(include_tables=False)
    check('clear() 后 evicted 归零', stc['evicted'] == 0,
          'evicted=%r' % (stc['evicted'],))
    check('clear() 后 dropped 也归零',
          stc['dropped'] == {'C2S': 0, 'S2C': 0},
          'dropped=%r' % (stc['dropped'],))

    # ── 6. stats 字段完整性（UI 依赖）──
    for key in ('evicted', 'ring_full', 'dropped', 'frames'):
        if key not in st:
            check('stats 缺少字段 %r' % key, False)
            break
    else:
        check('stats 暴露 UI 所需全部字段', True)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('丢帧计数：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
