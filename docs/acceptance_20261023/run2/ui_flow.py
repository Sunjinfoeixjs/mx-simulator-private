# -*- coding: utf-8 -*-
"""纯 UI 驱动（不持有任何子进程）：更新程序 → 公告 → 开始游戏 → 选服 → 登录 → 进世界。"""
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

WEB = 'http://127.0.0.1:8099/'
u = ui.u32


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


def click_updater_enter():
    w = win('梦想世界更新程序')
    if not w:
        return None
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
        if ww > 80 and hh > 40 and (best is None or r.top < best[2]):
            best = (r.left + ww // 2, r.top + hh // 2, r.top)
    if best:
        u.SetCursorPos(best[0], best[1])
        time.sleep(0.2)
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.08)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        print('click 进入游戏', best[:2], flush=True)
    return best


if wait_for(lambda: win('梦想世界更新程序'), 20):
    time.sleep(4)
    click_updater_enter()
    time.sleep(7)

w = wait_for(lambda: win(cls='Game'), 40)
if not w:
    print('NO GAME WINDOW', flush=True)
    raise SystemExit(2)
time.sleep(3)
ui.place(26, 26)
ui.click(407, 336, 1.0)
ui.shot('flow_1_notice')
ui.click(700, 257, 3.0)
ui.shot('flow_2_server')
ui.key(13, 3.5)
ui.shot('flow_3_login')
ui.type_text('123456')
ui.click(717, 449, 4.0)
ui.shot('flow_4_role')
ui.click(697, 449, 6.0)
ui.shot('flow_5_world')

try:
    st = json.loads(urllib.request.urlopen(WEB + 'api/state', timeout=6).read())
    print('connected', st.get('connected'), st.get('mapname'), st.get('px'), st.get('py'),
          'exp', st.get('exp'), flush=True)
except Exception as e:
    print('state ERR', repr(e), flush=True)
