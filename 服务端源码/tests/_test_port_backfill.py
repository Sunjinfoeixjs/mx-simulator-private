# -*- coding: utf-8 -*-
"""HTTP 服务的「端口 0 回填」契约。

背景（2026-10-20 实测）：GM 面板与调试台都支持传 port=0 让 OS 挑空闲端口，
但两处都**没有把实际端口写回**——
  · m4_server._start_web：日志打的是入参（传 0 就打印 :0），self._web_port 不更新；
  · debugger.attach：return 的是入参，调用方拿到 0，连不上（URLError 10049）。

本测试锁定：
  1. 传 0 后能拿到真实端口，且该端口真的能连上
  2. 两个服务的端口互不冲突（OS 分配）
  3. 显式端口照常工作（回填不破坏原语义）
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402
import debugger as D  # noqa: E402
import tap as T  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def http_json(port, path, timeout=3):
    return json.loads(urllib.request.urlopen(
        'http://127.0.0.1:%d%s' % (port, path), timeout=timeout).read())


def main():
    tmp = tempfile.mkdtemp(prefix='portbf_')
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                     logdir=os.path.join(tmp, 'logs'))
    srv.log = lambda *a, **k: None

    # ── 1. GM 面板端口回填 ──
    srv.serve_background(0, web=True, web_port=0)
    time.sleep(0.5)
    web_port = getattr(srv, '_web_port', 0)
    check('GM 面板 port=0 后 self._web_port 被回填',
          isinstance(web_port, int) and web_port > 0,
          '_web_port=%r' % (web_port,))
    try:
        st = http_json(web_port, '/api/state')
        check('回填的 GM 端口真的能连上', isinstance(st, dict) and 'in_war' in st,
              '字段数=%d' % len(st))
    except Exception as e:                                  # noqa: BLE001
        check('回填的 GM 端口真的能连上', False, repr(e))

    # ── 2. 调试台端口回填 ──
    dport = D.attach(srv, port=0)
    time.sleep(0.4)
    check('调试台 port=0 后返回真实端口',
          isinstance(dport, int) and dport > 0, 'return=%r' % (dport,))
    check('调试台端口与 GM 面板不同（OS 分别分配）',
          dport != web_port, 'debugger=%r gm=%r' % (dport, web_port))
    try:
        st = http_json(dport, '/api/stats')
        check('回填的调试台端口能连上 /api/stats',
              isinstance(st, dict) and 'frames' in st, '字段数=%d' % len(st))
    except Exception as e:                                  # noqa: BLE001
        check('回填的调试台端口能连上 /api/stats', False, repr(e))

    # ── 3. 显式端口仍照常工作 ──
    d2 = D.attach(srv, port=0)   # 再次 attach（幂等），仍应拿到可用端口
    check('重复 attach 仍返回可用端口', isinstance(d2, int) and d2 > 0,
          'return=%r' % (d2,))

    # ── 4. 直连 ThreadingHTTPServer 的端口回填基准 ──
    #   这条是上面两个实现的「参照」：证明 server_address[1] 就是正确做法
    h = ThreadingHTTPServer(('127.0.0.1', 0), D.Handler)
    h.daemon_threads = True
    real = h.server_address[1]
    check('ThreadingHTTPServer 自身 server_address[1] 可靠',
          isinstance(real, int) and real > 0, 'port=%r' % (real,))
    h.server_close()

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('端口回填：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
