# -*- coding: utf-8 -*-
"""临时：驱动启动器 → 登录界面 → 输入密码 → 登录，并逐步截图。"""
import ctypes
import time
from ctypes import wintypes

from PIL import ImageGrab

u = ctypes.WinDLL('user32', use_last_error=True)
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


def find(sub):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, lp):
        n = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(h, n, 256)
        if sub in n.value and u.IsWindowVisible(h):
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            out.append((h, n.value, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(cb, 0)
    return out[0] if out else None


def click(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.7)


def key(vk):
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, 0, 2, 0)
    time.sleep(0.06)


def wait(sub, sec=30):
    for _ in range(sec * 2):
        w = find(sub)
        if w:
            return w
        time.sleep(0.5)
    return None


w = wait('更新程序', 40)
print('launcher', w)
if w:
    h, _n, x, y, ww, hh = w
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(1)
    click(x + ww * 0.647, y + hh * 0.583)     # 进入游戏
    time.sleep(4)

w = wait('梦 想 世 界', 20)
print('login', w)
if w:
    h, _n, x, y, ww, hh = w
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(1)
    click(x + ww * 0.496, y + hh * 0.506)     # 密码框
    time.sleep(0.3)
    for d in '123456':
        key(0x30 + int(d))
    time.sleep(0.4)
    ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(r'C:\Users\Administrator\MX\_drive1.png')
    click(x + ww * 0.729, y + hh * 0.685)     # 登录
    time.sleep(8)
    ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(r'C:\Users\Administrator\MX\_drive2.png')
print('done')
