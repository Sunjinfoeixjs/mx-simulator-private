# -*- coding: utf-8 -*-
"""临时：选服 → 下一步 → 输入密码 → 登录（每步重新定位窗口，防窗口漂移）。"""
import ctypes
import time
from ctypes import wintypes

from PIL import ImageGrab

u = ctypes.WinDLL('user32', use_last_error=True)
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
SWP = 2 | 1 | 0x40


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
    return out[-1] if out else None


def front(w):
    h, _n, x, y, ww, hh = w
    u.SetWindowPos(h, -1, 0, 0, 0, 0, SWP)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.6)
    return w


def click(w, rx, ry, wait=0.9):
    h, _n, x, y, ww, hh = w
    u.SetCursorPos(int(x + rx), int(y + ry))
    time.sleep(0.2)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(wait)


def key(vk):
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, 0, 2, 0)
    time.sleep(0.06)


w = find('梦 想 世 界')
print('select', w)
front(w)
click(w, 405, 355)          # 确定（连接失败）
w = front(find('梦 想 世 界'))
click(w, 195, 597)          # 刷新列表
w = front(find('梦 想 世 界'))
click(w, 690, 145)          # 本地测试服（右栏）
w = front(find('梦 想 世 界'))
click(w, 396, 597, 2.5)     # 下一步

w = find('梦 想 世 界')
print('login', w)
if w:
    front(w)
    click(w, 400, 318, 0.4)     # 密码框
    for d in '123456':
        key(0x30 + int(d))
    time.sleep(0.4)
    click(w, 588, 431, 6)       # 登录
    w2 = front(find('梦 想 世 界'))
    h, _n, x, y, ww, hh = w2
    ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(r'C:\Users\Administrator\MX\_login_final.png')
    print('shot')
print('done')
