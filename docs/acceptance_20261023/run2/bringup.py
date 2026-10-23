# -*- coding: utf-8 -*-
"""把「服务端 + 客户端」一次性拉到进世界状态（中断后恢复用）。

前置：端口已清空（clean_ports.py）、boot.py 已在后台起好服务端。
步骤：patch/mx.bin → 更新程序「进入游戏」→ 公告「确定」→「开始游戏」→ 选服回车
      → 登录（补密码）→ 选角「进入游戏」→ 校验 /api/state.connected。
"""
import ctypes
import ctypes.wintypes as wintypes
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = r'D:\mxsj2013\client'
WEB = 'http://127.0.0.1:8099/'
u = ui.u32
k32 = ctypes.WinDLL('kernel32')


def atoms():
    k32.GlobalAddAtomA.restype = ctypes.c_ushort
    k32.GlobalAddAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalFindAtomA.restype = ctypes.c_ushort
    k32.GlobalFindAtomA.argtypes = [ctypes.c_char_p]
    k32.GlobalDeleteAtom.argtypes = [ctypes.c_ushort]
    for n in (b'mxpatch_run', b'mxpatch_shadow'):
        while k32.GlobalFindAtomA(n):
            k32.GlobalDeleteAtom(k32.GlobalFindAtomA(n))
        k32.GlobalAddAtomA(n)


def state():
    return json.loads(urllib.request.urlopen(WEB + 'api/state', timeout=6).read())


def win(title=None, cls=None):
    for w in ui.windows():
        if title and title in w['title']:
            return w
        if cls and w['class'] == cls:
            return w
    return None


def wait_for(fn, timeout=40, poll=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(poll)
    return None


def enter_game():
    """更新程序窗口里点「进入游戏」（按子按钮矩形定位，避免坐标漂移）。"""
    w = win('梦想世界更新程序')
    if not w:
        return False
    u.SetForegroundWindow(w['hwnd'])
    time.sleep(0.5)
    kids = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, lp):
        kids.append(h)
        return True

    u.EnumChildWindows(w['hwnd'], cb, 0)
    best = None
    for h in kids:
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value != 'Button':
            continue
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        ww, hh = r.right - r.left, r.bottom - r.top
        if ww > 80 and hh > 40:                     # 「进入游戏」是右下角最大的按钮
            if best is None or r.top < best[1]:
                best = (r.left + ww // 2, r.top + hh // 2, r.top)
    if not best:
        return False
    u.SetCursorPos(best[0], best[1])
    time.sleep(0.2)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    print('clicked 进入游戏 at', best[:2], flush=True)
    return True


atoms()
print('start patch/mx.bin', flush=True)
subprocess.Popen([os.path.join(CLIENT, 'patch', 'mx.bin')], cwd=CLIENT)

if not wait_for(lambda: win('梦想世界更新程序'), 25):
    print('no updater window', flush=True)
else:
    time.sleep(5)
    enter_game()

if not wait_for(lambda: win(cls='Game'), 45):
    print('NO GAME WINDOW', flush=True)
    raise SystemExit(2)
time.sleep(4)
ui.place(26, 26)
time.sleep(1)

# 公告弹窗 → 开始游戏
ui.click(407, 336, 1.2)
ui.shot('bring_1_launcher')
ui.click(700, 257, 3.5)
ui.shot('bring_2_server')

# 选服 → 登录
ui.key(13, 4.0)
ui.shot('bring_3_login')
ui.type_text('123456')          # 登录框默认聚焦密码
ui.shot('bring_4_pw')
ui.click(717, 449, 4.5)
ui.shot('bring_5_role')

# 选角 → 进世界
ui.click(697, 449, 6.0)
ui.shot('bring_6_world')

try:
    st = state()
    print('connected', st.get('connected'), 'map', st.get('mapname'),
          'pos', st.get('px'), st.get('py'), 'exp', st.get('exp'), flush=True)
except Exception as e:
    print('state ERR', repr(e), flush=True)
