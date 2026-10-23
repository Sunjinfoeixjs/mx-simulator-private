# -*- coding: utf-8 -*-
"""存档增量 / 异步落盘 —— 回归测试（进程内 + 真 socket 各一半）。

覆盖：
  ① 同步模式（SAVE_ASYNC=False）行为不变：_persist 返回即已落盘，文件内容正确
  ② 异步模式：_persist 不阻塞；flush_saves 后文件已写好且内容 == 同步模式
  ③ 增量：连续两次 persist 无变化 → 不写盘（noop 计数）
  ④ 合并：一条业务链里连续 persist → 只写最后一份（coalesced 计数）
  ⑤ 停机/断线收口：stop() 后队列必须空、文件为最新快照
  ⑥ 跨写者冲突拒写（另一会话写过 → 保留磁盘进度）
  ⑦ save_stats 自洽

用法：python _test_save_async.py
"""
from __future__ import print_function

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
for p in (SIM, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import m4_server as m                       # noqa: E402
import tap as T                             # noqa: E402

ok = True


def check(c, msg):
    global ok
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, f):
        self.sent.append(f)


_seq = [0]


def make_server(tmp, save_async):
    _seq[0] += 1
    sub = os.path.join(tmp, 'case%d' % _seq[0])
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'logs'), savedir=os.path.join(sub, 'save'))
    srv._arm_cmd_timer = lambda: None
    srv._save_async = save_async
    if save_async:
        srv._ensure_save_writer()
    srv.mapid = 1025
    srv.grade = 10
    srv.px, srv.py = 100, 100
    srv._arrive_xy = (100, 100)
    return srv


def test_sync_unchanged(tmp):
    print('── ① 同步模式行为不变 ──')
    srv = make_server(tmp, save_async=False)
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', 3021))
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', 1))
    check(101 in srv.tasks, '已接主线 101')
    # ★ 注意：接任务时 _quest_accept 已经落过一次盘，所以这里必须先改点东西，
    #   否则这次 _persist 会被增量判定为「无变化」直接跳过（那正是要测的行为）。
    srv.px = 101
    # 同步模式：_persist 返回时文件一定已写好
    r = srv._persist()
    check(r is True, '_persist() 返回 True（已落盘）')
    sv = m.load_role(srv.savepath)
    check(isinstance(sv, dict) and sv.get('px') == 101,
          '文件里就是当前状态（px=%s）' % (sv or {}).get('px'))
    check('101' in (sv.get('tasks') or {}) or 101 in (sv.get('tasks') or {}),
          '任务态已落盘')
    check(srv.save_stats()['async'] is False, 'save_stats 如实报告同步模式')


def test_async_and_flush(tmp):
    print('── ② 异步 + flush ──')
    srv = make_server(tmp, save_async=True)
    conn = FakeConn()
    srv._dispatch(conn, m.C2S_NPC_LOOK, struct.pack('<I', 3021))
    srv._dispatch(conn, m.C2S_NPC_ANSWER, struct.pack('<I', 1))
    srv.px = 123
    r = srv._persist()
    check(r is None, '异步模式 _persist() 不返回落盘结果（返回 None）')
    # 此刻文件可能还没写（不写死，因为写线程可能已经跑完）
    okf = srv.flush_saves(5.0)
    check(okf is True, 'flush_saves() 等到写线程完成')
    sv = m.load_role(srv.savepath)
    check((sv or {}).get('px') == 123, 'flush 后文件是**最新**快照（px=%s）'
          % (sv or {}).get('px'))
    st = srv.save_stats()
    check(st['writes'] >= 1, '至少写过一次（writes=%d）' % st['writes'])
    check(st['pending'] == 0, 'flush 后队列排空（pending=%d）' % st['pending'])


def test_incremental_noop(tmp):
    print('── ③ 增量：无变化不写盘 ──')
    srv = make_server(tmp, save_async=False)
    srv._persist()                            # 第一次：全量
    w0 = srv._save_writes
    for _ in range(5):
        srv._persist()                        # 什么都没改
    check(srv._save_writes == w0, '5 次无变化 persist 一次都没写盘（writes %d→%d）'
          % (w0, srv._save_writes))
    check(srv.save_stats()['noop'] == 5, 'noop 计数 = 5（%d）' % srv.save_stats()['noop'])
    srv.px = 200                              # 改一个键
    srv._persist()
    check(srv._save_writes == w0 + 1, '改了一个键就写（脏键检测生效）')


def test_coalesce(tmp):
    print('── ④ 异步合并：连续 persist 只写最后一份 ──')
    srv = make_server(tmp, save_async=True)
    srv._persist()
    srv.flush_saves(5.0)
    w0 = srv._save_writes
    for i in range(6):                        # 模拟一条业务链里连续落盘
        srv.px = 300 + i
        srv._persist()
    srv.flush_saves(5.0)
    st = srv.save_stats()
    sv = m.load_role(srv.savepath)
    check((sv or {}).get('px') == 305, '文件里是最后一份快照（px=%s）'
          % (sv or {}).get('px'))
    check(st['coalesced'] >= 1, '中间快照被合并掉（coalesced=%d）' % st['coalesced'])
    check(st['writes'] < w0 + 6, '写入次数明显少于 persist 次数（%d < %d）'
          % (st['writes'], w0 + 6))
    check(st['pending'] == 0, '队列已排空')


def test_shutdown_flush(tmp):
    print('── ⑤ 停机/断线收口 ──')
    sub = os.path.join(tmp, 'shut')
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(sub, 'l'), savedir=os.path.join(sub, 's'))
    srv._arm_cmd_timer = lambda: None
    srv._save_async = True
    srv._ensure_save_writer()
    srv.px = 777
    srv._persist()
    check(srv.save_stats()['pending'] >= 1, '停机前排了写任务（pending=%d）'
          % srv.save_stats()['pending'])
    srv.stop()
    st = srv.save_stats()
    check(st['pending'] == 0, 'stop() 后队列排空')
    sv = m.load_role(srv.savepath)
    check((sv or {}).get('px') == 777, 'stop() 后文件是最新快照（px=%s）'
          % (sv or {}).get('px'))
    check(srv._save_thread is None, '写线程已停止')


def test_conflict_still_detected(tmp):
    print('── ⑥ 跨写者冲突拒写 ──')
    srv = make_server(tmp, save_async=True)
    srv._persist()
    srv.flush_saves(5.0)
    # 模拟另一个会话写了同一个文件
    other = m.load_role(srv.savepath)
    other['_save_tag'] = 'someone-else'
    m.save_role(other, srv.savepath)
    disk_before = m.load_role(srv.savepath)
    srv.px = 888
    srv._persist()
    srv.flush_saves(5.0)
    check(srv._save_conflict is True, '检测到被另一会话写过并置 _save_conflict')
    disk_after = m.load_role(srv.savepath)
    check(disk_after.get('px') == disk_before.get('px'),
          '冲突时拒绝覆盖（磁盘 px=%s，本会话 px=888）'
          % disk_after.get('px'))
    check(srv._save_last_ok is False, '冲突拒写如实上报失败')


def test_stats_selfcheck(tmp):
    print('── ⑦ save_stats 自洽 ──')
    srv = make_server(tmp, save_async=True)
    srv._persist(); srv.flush_saves(5.0)
    srv._persist(); srv.flush_saves(5.0)      # 无变化 → noop
    st = srv.save_stats()
    check(set(['async', 'pending', 'dirty_keys', 'writes', 'coalesced',
               'noop', 'saved_io']) <= set(st), '字段齐全：%s' % sorted(st))
    check(st['saved_io'] == st['coalesced'] + st['noop'],
          'saved_io = coalesced + noop（%d = %d + %d）'
          % (st['saved_io'], st['coalesced'], st['noop']))
    check(isinstance(st['dirty_keys'], int), 'dirty_keys 是整数')


def _save_dump(srv):
    """失败时把存档目录的真实情况带出来（路径 + 每个 role*.json 的 px）。

    ★ 诊断信息必须进断言消息：这类偶发失败如果只报「px=None」，第一反应会是
      「保存丢帧」，但很可能是**读错了文件**（多账号档名 role_<账号>.json）。
      多打一行路径，能把这两种原因一眼分开。
    """
    try:
        d = srv.savedir
        names = sorted(os.listdir(d)) if os.path.isdir(d) else []
        parts = []
        for n in names:
            if not n.endswith('.json'):
                continue
            try:
                data = m.load_role(os.path.join(d, n)) or {}
                parts.append('%s:px=%s' % (n, data.get('px')))
            except Exception:                   # noqa: BLE001
                parts.append('%s:读失败' % n)
        return '｜savepath=%s｜目录=[%s]' % (os.path.basename(srv.savepath),
                                          ', '.join(parts) or '空')
    except Exception as e:                      # noqa: BLE001
        return '｜诊断失败：%r' % e


def test_e2e_disconnect(tmp):
    print('── ⑧ 真连接：断线前那次 persist 不丢 ──')
    sub = os.path.join(tmp, 'e2e')
    srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0,
                     enemies=1, turn_timeout=30,
                     logdir=os.path.join(sub, 'l'), savedir=os.path.join(sub, 's'))
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.4)
    c = socket.create_connection(('127.0.0.1', srv.port), timeout=5)
    c.settimeout(5)

    def exact(n):
        b = b''
        while len(b) < n:
            ch = c.recv(n - len(b))
            if not ch:
                raise ConnectionError
            b += ch
        return b

    def recv():
        n = exact(1)[0]
        if n == 0xFF:
            n = struct.unpack('<H', exact(2))[0]
        return exact(n)

    def send(op, body=b''):
        c.sendall(T.make_c2s(op, body))

    def drain(idle=0.4, max_wait=8.0):
        """把服务端这一轮的回帧收干净（静默 idle 秒即认为这一轮结束）。

        ★ 原来这里是 `time.sleep(0.3)` 硬等：走全量回归时 CPU 被 coverage_smoke
          之类占满，服务端线程可能还没处理完上一条指令就被关连接，于是
          「建角/进世界/走一步」里任意一步丢掉 → 存档根本没生成（px=None）。
          改成「等回帧静默」后，慢机器只是多等一会儿，断言强度不变。
        """
        old = c.gettimeout()
        c.settimeout(idle)
        t0 = time.time()
        try:
            while time.time() - t0 < max_wait:
                try:
                    recv()
                except socket.timeout:
                    break                       # 静默 idle 秒 = 这一轮发完了
                except Exception:               # noqa: BLE001
                    break
        finally:
            c.settimeout(old)

    recv()
    c.sendall(m.hello_response(0x12345678))
    drain()
    send(3, u'测试'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 5)
    drain()
    # ★ 2026-10-23：全量跑到 58 步之后，这条用例在全量里偶发「存档目录全空」——
    #   不是丢帧，是**握手根本没走完**（CPU 被别的步骤占满时，drain 判静默就提前返回）。
    #   drain 只看"多久没新帧"，不看"角色建起来没有"。所以补两道**状态**等待：
    #   op3 之后等 hero_name 落库，op2 之后等背包发下来。
    def wait_for(cond, what, max_wait=10.0):
        t0 = time.time()
        while time.time() - t0 < max_wait:
            if cond():
                return True
            time.sleep(0.02)
        print('    ⚠ 等 %s 超时（%.0fs）' % (what, max_wait))
        return False

    wait_for(lambda: bool(getattr(srv, 'hero_name', '')), '角色名落库')
    send(2, struct.pack('<I', 1))
    drain()
    wait_for(lambda: bool(getattr(srv, 'items', None)), '背包下发')
    # 走一步（会触发 _persist：位置变了）
    send(54, struct.pack('<HH', 46, 40) + b'\x00')
    drain()
    # ★ 2026-10-23：改成**等条件**，不再靠注释里那句「drain 完就稳了」。
    #   全量回归时（58 步之后负载更高）偶发 px=None：进世界那条指令还没处理完
    #   就 close，角色都没建起来，存档自然是空的。等 srv.px 真的变成 46 再断，
    #   慢机器只是多等，断言强度不变。
    t_wait = time.time()
    while time.time() - t_wait < 8.0 and getattr(srv, 'px', None) != 46:
        time.sleep(0.02)
    c.close()                                  # 立刻断开：不等服务端自己收尾
    # ★ 2026-10-23 顺序修正：**先等落盘，再 stop()**。
    #   stop() 会关连接并置停机标志（`_SHUTTING_DOWN`），若断线收尾那次 persist
    #   还没跑，就会被停机逻辑掐掉 —— 表现是「存档目录全空」，看起来像丢帧，
    #   实际是我们自己把写盘关在了门外。全量负载高时这个窗口最容易踩上。
    sv = {}
    t_wait = time.time()
    while time.time() - t_wait < 8.0:
        sv = m.load_role(srv.savepath) or {}
        if sv.get('px') == 46:
            break
        time.sleep(0.05)
    srv.stop()
    if sv.get('px') != 46:
        # stop 的停机 flush 可能补上最后一笔
        t_wait = time.time()
        while time.time() - t_wait < 3.0:
            sv = m.load_role(srv.savepath) or {}
            if sv.get('px') == 46:
                break
            time.sleep(0.05)
    check((sv or {}).get('px') == 46,
          '断线后文件里是最后位置（px=%s，期望 46）%s'
          % ((sv or {}).get('px'), _save_dump(srv)))


def main():
    tmp = tempfile.mkdtemp(prefix='m4save_')
    try:
        test_sync_unchanged(tmp)
        test_async_and_flush(tmp)
        test_incremental_noop(tmp)
        test_coalesce(tmp)
        test_shutdown_flush(tmp)
        test_conflict_still_detected(tmp)
        test_stats_selfcheck(tmp)
        test_e2e_disconnect(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'HAS FAILURE'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
