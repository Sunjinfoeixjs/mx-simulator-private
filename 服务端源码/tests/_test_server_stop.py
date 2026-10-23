# -*- coding: utf-8 -*-
"""M4Server 的优雅停止（stop / atexit）。

背景（2026-10-20）：`run()` 是 `while True` 死循环，**没有任何退出路径**。
测试脚本把它放进 daemon 线程，主线程一结束解释器就直接强杀该线程——
Windows 上表现为进程以 0xC0000409（STATUS_STACK_BUFFER_OVERRUN）退出，
看着像崩溃，实为「带 socket 的线程被硬杀」。全量回归里偶发 1/10。

本测试锁定：
  1. stop() 能让阻塞在 accept() 的 run() 线程真正退出
  2. 停止后端口可被重新绑定（监听 socket 真的关了）
  3. stop() 可重复调用（幂等，不抛）
  4. 未启动的实例调 stop() 不崩
"""
import os
import socket
import sys
import tempfile
import threading
import time

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


def main():
    tmp = tempfile.mkdtemp(prefix='stop_')
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                     logdir=os.path.join(tmp, 'logs'))
    srv.log = lambda *a, **k: None
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    time.sleep(0.4)
    port = srv.port
    check('服务已启动', t.is_alive() and port > 0, 'port=%r' % (port,))

    # ── 1. stop() 让线程退出 ──
    srv.stop()
    t.join(timeout=3)
    check('stop() 后 run 线程退出', not t.is_alive(),
          'alive=%r' % (t.is_alive(),))

    # ── 2. 端口可重新绑定 ──
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', port))
        rebound = True
    except OSError as e:
        rebound = False
        err = str(e)
    finally:
        s.close()
    check('停止后端口可重新绑定（监听真的关了）', rebound,
          '' if rebound else 'err=%r' % (err,))

    # ── 3. 重复 stop 幂等 ──
    try:
        srv.stop()
        srv.stop()
        check('stop() 可重复调用（幂等）', True)
    except Exception as e:                                  # noqa: BLE001
        check('stop() 可重复调用（幂等）', False, repr(e))

    # ── 4. 未启动实例 stop 不崩 ──
    srv2 = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save2'),
                      logdir=os.path.join(tmp, 'logs2'))
    srv2.log = lambda *a, **k: None
    try:
        srv2.stop()
        check('未启动实例 stop() 不崩', True)
    except Exception as e:                                  # noqa: BLE001
        check('未启动实例 stop() 不崩', False, repr(e))

    # ── 5. atexit 钩子已注册 ──
    check('atexit 钩子已武装（_atexit_armed）',
          getattr(srv, '_atexit_armed', False) is True,
          '_atexit_armed=%r' % (getattr(srv, '_atexit_armed', None),))

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('优雅停止：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
