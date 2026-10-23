# -*- coding: utf-8 -*-
"""一站式：起服务端 → 起客户端 → 驱动登录进世界 → 跑 33 分钟稳定性测试。

为什么写成一个进程：本机会话中断会带走后台任务的整棵进程树，把「服务端 + 客户端 +
采样器」放进同一个常驻进程里，中断只会中断这一次运行，不会留下半死不活的状态。
用服务端日志里的关键行做状态机信号，避免盲目 sleep：
  Hello PASS → 已连上；登录: → 登录成功；选角进入 → 已在世界。
"""
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

CLIENT = r'D:\mxsj2013\client'
LOG = os.path.join(HERE, 'server.log')
RUNLOG = os.path.join(HERE, 'run_all.log')
WEB = 'http://127.0.0.1:8099/'
SOAK_SEC = int(os.environ.get('SOAK_SEC', '2000'))


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    with io.open(RUNLOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def tail(n=400):
    try:
        with io.open(LOG, encoding='utf-8', errors='replace') as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def since(mark, text, limit=600):
    """mark 之后是否出现过 text。"""
    lines = tail(limit)
    for i in range(len(lines) - 1, -1, -1):
        if mark in lines[i]:
            return any(text in l for l in lines[i:])
    return False


def state():
    return json.loads(urllib.request.urlopen(WEB + 'api/state', timeout=6).read())


def win(cls='Game'):
    for w in ui.windows():
        if w['class'] == cls:
            return w
    return None


def wait(fn, timeout=40, poll=0.4):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(poll)
    return None


def atoms():
    k32 = ctypes.WinDLL('kernel32')
    k32.GlobalAddAtomA.restype = ctypes.c_ushort
    k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalFindAtomA.restype = ctypes.c_ushort
    k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]
    for n in (b'mxpatch_run', b'mxpatch_shadow'):
        while k32.GlobalFindAtomA(n):
            k32.GlobalDeleteAtom(k32.GlobalFindAtomA(n))
        k32.GlobalAddAtomA(n)


def click_updater_enter():
    w = None
    for x in ui.windows():
        if '梦想世界更新程序' in x['title']:
            w = x
            break
    if not w:
        return False
    u = ui.u32
    u.SetForegroundWindow(w['hwnd'])
    time.sleep(0.5)
    import ctypes.wintypes as wt
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, lp):
        kids.append(h)
        return True

    u.EnumChildWindows(w['hwnd'], cb, 0)
    cands = []
    for k in kids:
        c = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(k, c, 64)
        if c.value != 'Button':
            continue
        r = wt.RECT()
        u.GetWindowRect(k, ctypes.byref(r))
        if r.right - r.left > 80 and r.bottom - r.top > 40:
            cands.append((r.top, r.left + (r.right - r.left) // 2,
                          r.top + (r.bottom - r.top) // 2))
    cands.sort()
    if not cands:
        return False
    u.SetCursorPos(cands[0][1], cands[0][2])
    time.sleep(0.25)
    u.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.1)
    u.mouse_event(4, 0, 0, 0, 0)
    log('click 进入游戏 at %s' % (cands[0][1:],))
    return True


def normalize_window():
    w = win()
    if not w:
        return None
    ui.u32.ShowWindow(w['hwnd'], 9)
    time.sleep(0.4)
    ui.u32.SetWindowPos(w['hwnd'], -1, 40, 40, 0, 0, 0x0001)
    time.sleep(0.3)
    ui.u32.SetForegroundWindow(w['hwnd'])
    time.sleep(0.5)
    return win()


def main():
    open(RUNLOG, 'a').close()
    log('=== run_all start, soak=%ss ===' % SOAK_SEC)

    # ① 服务端
    subprocess.run([sys.executable, 'clean_ports.py'], cwd=HERE,
                   stdout=subprocess.DEVNULL)
    srv = subprocess.Popen([sys.executable, '-u', 'boot.py'], cwd=HERE)
    log('boot pid %d' % srv.pid)
    if not wait(lambda: _port_open(), 30):
        log('FATAL server not listening')
        return 2
    log('server up')

    # ② 客户端（最多 3 次；启动器偶发拉不起游戏窗口时重来）
    cli = None
    for attempt in (1, 2, 3):
        atoms()
        cli = subprocess.Popen([os.path.join(CLIENT, 'patch', 'mx.bin')], cwd=CLIENT)
        log('client try %d pid %d' % (attempt, cli.pid))
        if wait(lambda: _updater(), 25):
            time.sleep(5)
            click_updater_enter()
        if wait(lambda: win(), 60):
            break
        log('no game window on try %d, retry' % attempt)
        try:
            cli.kill()
        except Exception:
            pass
        subprocess.run(['taskkill', '/F', '/T', '/IM', 'mx.bin'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    if not win():
        log('FATAL no game window after retries')
        return 3
    normalize_window()
    time.sleep(3)

    # ③ 公告 → 开始游戏（布局偶有 15px 漂移，三个候选点依次点）
    ui.key(13, 1.5)
    before = len(tail(200))
    for y in (269, 284, 299):
        ui.click(697, y, 1.6)
        if since('接入', 'Hello PASS'):
            break
    if not wait(lambda: since('接入', 'Hello PASS'), 20):
        log('WARN no Hello after start click, retry once')
        for y in (284, 269):
            ui.click(697, y, 2.0)
            if since('接入', 'Hello PASS'):
                break

    # ④ 选服 → 登录
    ui.key(13, 3.5)
    ui.type_text('123456')
    ui.key(13, 4.0)
    if not wait(lambda: since('登录:', '登录:'), 15):
        log('WARN login not seen, retry')
        ui.click(717, 449, 4.0)
    if wait(lambda: since('登录:', '已回 AccountInfo'), 15):
        log('login ok')

    # ⑤ 选角进世界
    for _ in range(3):
        ui.click(697, 489, 5.0)
        if since('选角进入', '选角进入'):
            break
        ui.key(13, 3.0)
    if not wait(lambda: since('选角进入', '选角进入'), 15):
        log('FATAL not in world')
        return 4
    log('in world')
    time.sleep(3)

    # ⑥ 切野外 + 稳定性
    try:
        req = urllib.request.Request(WEB + 'api/map',
                                     data=json.dumps({'mapid': 1005, 'x': 80, 'y': 60}).encode(),
                                     headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=6).read()
        log('goto 天水平原')
    except Exception as e:
        log('goto map ERR %r' % (e,))
    time.sleep(2)

    import soak
    soak.DURATION = SOAK_SEC
    soak.main()
    log('=== run_all done ===')
    return 0


def _port_open():
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 12239))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _updater():
    for w in ui.windows():
        if '梦想世界更新程序' in w['title']:
            return True
    return False


if __name__ == '__main__':
    sys.exit(main())
