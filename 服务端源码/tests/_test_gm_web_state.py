# -*- coding: utf-8 -*-
"""GM 面板启动状态 GM_WEB_STATE + 横幅可见性。

背景（2026-10-20）：面板起不来时旧实现只打一行日志就返回 False，进程照常跑——
用户以为面板开了、浏览器打不开、回头看日志又刷过去了。

本测试锁定：
  1. 三种状态取值正确（disabled / ready / failed）
  2. 横幅能反映状态（成功显示端口，失败显示原因）
  3. port=0（交给 OS）**不能**被误判为失败
  4. 端口全占时 reason='failed' 且 message 说明原因
  5. /api/features 带 gm_web
"""
import json
import os
import socket
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


def banner_web_line():
    """取横幅里与 GM 面板相关的**所有**行（失败时是两行：原因 + 内核不受影响）。"""
    lines = [l.strip() for l in m.dmg_banner().split(chr(10))
             if 'GM 面板' in l or '内核不受影响' in l]
    return chr(10).join(lines)


def main():
    tmp = tempfile.mkdtemp(prefix='gmweb_')

    # ── 1. disabled：没加 --web ──
    m.GM_WEB_STATE.update(enabled=False, reason='disabled', port=0,
                          want=0, bumped=False, message='')
    check("未启用时 reason='disabled'",
          m.GM_WEB_STATE['reason'] == 'disabled')
    check('横幅显示「未启用」', '未启用' in banner_web_line(),
          banner_web_line())

    # ── 2. ready：port=0 交给 OS ──
    #   ★ 这是首版踩过的坑：port=0 时 reason='OS 自动分配' 曾被误判为 failed
    srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 's1'),
                     logdir=os.path.join(tmp, 'l1'))
    srv.log = lambda *a, **k: None
    srv.serve_background(0, web=True, web_port=0)
    time.sleep(0.6)
    check("port=0 启动后 reason='ready'（不误判为 failed）",
          m.GM_WEB_STATE['reason'] == 'ready',
          'reason=%r' % (m.GM_WEB_STATE['reason'],))
    check('ready 时 port > 0 且等于 _web_port',
          m.GM_WEB_STATE['port'] == srv._web_port > 0,
          'state.port=%r _web_port=%r' % (m.GM_WEB_STATE['port'], srv._web_port))
    check('横幅显示已启动 + 端口',
          '已启动' in banner_web_line() and str(srv._web_port) in banner_web_line(),
          banner_web_line())

    # ── 3. /api/features 带 gm_web ──
    try:
        d = json.loads(urllib.request.urlopen(
            'http://127.0.0.1:%d/api/features' % srv._web_port,
            timeout=3).read())
        check('/api/features 带 gm_web',
              isinstance(d.get('gm_web'), dict), 'gm_web=%r' % (d.get('gm_web'),))
        check('gm_web 里的 port 与实际一致',
              (d.get('gm_web') or {}).get('port') == srv._web_port)
    except Exception as e:                                  # noqa: BLE001
        check('/api/features 带 gm_web', False, repr(e))
    finally:
        try:
            srv.stop()
        except Exception:                                   # noqa: BLE001
            pass

    # ── 4. failed：端口全占 ──
    blockers = []
    base = 18300
    for pt in range(base, base + 25):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', pt))
            s.listen(1)
            blockers.append(s)
        except OSError:
            pass
    try:
        srv2 = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 's2'),
                          logdir=os.path.join(tmp, 'l2'))
        srv2.log = lambda *a, **k: None
        srv2.serve_background(0, web=True, web_port=base)
        time.sleep(0.6)
        check("端口全占时 reason='failed'",
              m.GM_WEB_STATE['reason'] in ('failed', 'error'),
              'reason=%r' % (m.GM_WEB_STATE['reason'],))
        check('failed 时 port=0（没有监听）',
              m.GM_WEB_STATE['port'] == 0,
              'port=%r' % (m.GM_WEB_STATE['port'],))
        check('failed 时 message 说明原因',
              bool(m.GM_WEB_STATE.get('message')),
              'message=%r' % (m.GM_WEB_STATE.get('message'),))
        check('横幅显示启动失败 + 原因',
              '启动失败' in banner_web_line(), banner_web_line())
        check('横幅说明「游戏内核不受影响」',
              '内核不受影响' in banner_web_line())
        try:
            srv2.stop()
        except Exception:                                   # noqa: BLE001
            pass
    finally:
        for b in blockers:
            b.close()

    # ── 5. 探测路径列表（probed）：排查用 ──
    #   光看「顺延到 8081」不知道 8080 是「被占」还是「探测坏了」。
    srv3 = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 's3'),
                      logdir=os.path.join(tmp, 'l3'))
    logs3 = []
    srv3.log = lambda s, _L=logs3: _L.append(s)
    p3 = 18450
    b3 = None
    for cand in range(p3, p3 + 40):
        try:
            b3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            b3.bind(('127.0.0.1', cand))
            b3.listen(1)
            p3 = cand
            break
        except OSError:
            try:
                b3.close()
            except Exception:                           # noqa: BLE001
                pass
            b3 = None
    try:
        srv3.serve_background(0, web=True, web_port=p3)
        time.sleep(0.6)
        _probed = m.GM_WEB_STATE.get('probed') or []
        check('GM_WEB_STATE 记录探测路径 probed',
              isinstance(_probed, list) and len(_probed) >= 2,
              '%d 条' % len(_probed))
        check('probed 每条含 port/state 且是 dict（可 JSON 序列化）',
              all(isinstance(x, dict) and 'port' in x and 'state' in x
                  for x in _probed),
              _probed[:2])
        check('probed 首条是被占的那个端口',
              bool(_probed) and _probed[0]['port'] == p3
              and _probed[0]['state'] == 'busy',
              _probed[:1])
        check('probed 末条是最终采用的端口（free）',
              bool(_probed) and _probed[-1]['state'] == 'free'
              and _probed[-1]['port'] == srv3._web_port,
              _probed[-1:])
        # ★ 成功分支也必须写 probed：只写失败分支的话，正常启动时
        #   面板起来了却查不到「试过哪些端口」，排查照样抓瞎。
        check('成功启动时 probed 也写进状态（不只失败才写）',
              len(_probed) >= 1
              and any(x['state'] == 'busy' for x in _probed),
              '条数=%d' % len(_probed))
    finally:
        if b3:
            b3.close()
        try:
            srv3.stop()
        except Exception:                               # noqa: BLE001
            pass

    # ── 6. 失败时探测表要打到日志里（用户能看到试过哪些） ──
    _fail_logs = [l for l in logs3 if '已检查' in l or '占用：' in l]
    # 上面那个场景是成功的，失败日志在场景 4 里；这里只断言格式化函数可用
    _fmt = srv3._format_probe_log()
    check('_format_probe_log 能产出多行摘要',
          isinstance(_fmt, list) and any('已检查' in x for x in _fmt),
          _fmt)
    # 连续区间压缩：手工塞 5 个连续 busy，应显示 "a-b" 而不是逐个罗列
    srv3._port_probe_log = [(19000 + i, 'busy') for i in range(5)] + \
                           [(19005, 'free')]
    _fmt2 = srv3._format_probe_log()
    check('摘要压缩连续端口区间（5 个连续 → a-b 形式）',
          any('19000-19004' in x for x in _fmt2), _fmt2)
    check('摘要里可用端口单独列出',
          any('19005' in x and '可用' in x for x in _fmt2), _fmt2)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('GM 面板状态：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
