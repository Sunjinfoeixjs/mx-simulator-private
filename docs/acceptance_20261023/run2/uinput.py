# -*- coding: utf-8 -*-
"""原版客户端实机验收：输入/截图/窗口控制的最小封装（run2）。

与上一轮 wintool.py 的差异：
  · type_text() 支持字母（VkKeyScanW 自动处理 Shift），上一轮只能打数字；
  · 每次点击前强制置顶 + 前置，避免 IDE 盖住客户区导致点击落空；
  · shot() 统一写到 run2/shots/，文件名带序号，便于按时间核对。
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

from PIL import ImageGrab

u32 = ctypes.windll.user32
u32.FindWindowW.restype = wt.HWND
u32.GetForegroundWindow.restype = wt.HWND
u32.WindowFromPoint.restype = wt.HWND
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u32.SetWindowPos.restype = wt.BOOL
u32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(HERE, 'shots')
os.makedirs(SHOT_DIR, exist_ok=True)
SEQ = {'n': 0}

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002


def windows(visible_only=True):
    rows = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, lp):
        n = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, n, 512)
        u32.GetClassNameW(h, cls, 256)
        vis = bool(u32.IsWindowVisible(h))
        if (vis or not visible_only) and (n.value or cls.value):
            r = wt.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            rows.append({'hwnd': h, 'visible': vis, 'class': cls.value, 'title': n.value,
                         'rect': (r.left, r.top, r.right - r.left, r.bottom - r.top)})
        return True

    u32.EnumWindows(cb, 0)
    return rows


def client_win():
    hits = [w for w in windows() if w['class'] == 'Game']
    return hits[-1] if hits else None


def wait_client(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        w = client_win()
        if w:
            return w
        time.sleep(0.4)
    return None


def rect(w):
    r = wt.RECT()
    u32.GetWindowRect(w['hwnd'], ctypes.byref(r))
    w['rect'] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return w


def bring(w=None):
    w = w or client_win()
    h = w['hwnd']
    u32.ShowWindow(h, 9)
    u32.SetWindowPos(wt.HWND(h), wt.HWND(-1), 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)
    u32.BringWindowToTop(h)
    u32.SetForegroundWindow(h)
    time.sleep(0.35)
    return rect(w)


def origin(w):
    pt = wt.POINT(0, 0)
    u32.ClientToScreen(w['hwnd'], ctypes.byref(pt))
    return pt.x, pt.y


def shot(name, pad=0):
    w = bring()
    x, y, ww, hh = w['rect']
    img = ImageGrab.grab(bbox=(x - pad, y - pad, x + ww + pad, y + hh + pad))
    SEQ['n'] += 1
    path = os.path.join(SHOT_DIR, '%02d_%s.png' % (SEQ['n'], name))
    img.save(path)
    print('SHOT', path, img.size, flush=True)
    return path


def activate(w):
    """把窗口真正激活：只 SetForegroundWindow 时客户端仍按「非激活」丢弃鼠标点击
    （实机验证：必须再点一下标题栏，登录/选角界面的按钮才响应）。"""
    h = w['hwnd']
    if u32.GetForegroundWindow() == h:
        return
    u32.SetForegroundWindow(h)
    time.sleep(0.15)
    x, y, ww, _hh = w['rect']
    u32.SetCursorPos(int(x + ww / 2), int(y + 10))
    time.sleep(0.12)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.35)


def click(rx, ry, wait=0.8, w=None):
    w = bring(w or client_win())
    activate(w)
    ox, oy = origin(w)
    u32.SetCursorPos(int(ox + rx), int(oy + ry))
    time.sleep(0.12)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(wait)


def dclick(rx, ry, wait=1.2):
    click(rx, ry, 0.12)
    click(rx, ry, wait)


def rclick(rx, ry, wait=0.8, w=None):
    """右键：战斗里先右键进入「选择攻击目标」，再左键点敌人。"""
    w = bring(w or client_win())
    ox, oy = origin(w)
    u32.SetCursorPos(int(ox + rx), int(oy + ry))
    time.sleep(0.12)
    u32.mouse_event(0x0008, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0010, 0, 0, 0, 0)
    time.sleep(wait)


VK = {'alt': 0x12, 'ctrl': 0x11, 'shift': 0x10, 'esc': 0x1B, 'enter': 0x0D,
      'tab': 0x09, 'space': 0x20, 'back': 0x08, 'del': 0x2E}


def key(vk, wait=0.06):
    u32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    u32.keybd_event(vk, 0, 2, 0)
    time.sleep(wait)


def combo(*names):
    """combo('alt','a') → 按住 Alt 后按 A。"""
    vks = [VK.get(n.lower(), ord(n.upper()) if len(n) == 1 else 0) for n in names]
    for v in vks[:-1]:
        u32.keybd_event(v, 0, 0, 0)
        time.sleep(0.03)
    key(vks[-1])
    for v in reversed(vks[:-1]):
        u32.keybd_event(v, 0, 2, 0)
        time.sleep(0.03)
    time.sleep(0.15)


def _char_vk(ch):
    res = u32.VkKeyScanW(ctypes.c_wchar(ch))
    if res == -1:
        raise ValueError('no vk for %r' % ch)
    vk = res & 0xFF
    shift = bool(res & 0x100)
    return vk, shift


def type_text(text, gap=0.06):
    for ch in text:
        vk, shift = _char_vk(ch)
        if shift:
            u32.keybd_event(0x10, 0, 0, 0)
            time.sleep(0.02)
        key(vk, gap)
        if shift:
            u32.keybd_event(0x10, 0, 2, 0)
            time.sleep(0.02)


def clear_field(rx, ry, times=20):
    click(rx, ry, 0.25)
    u32.keybd_event(0x11, 0, 0, 0)
    key(0x41, 0.05)
    u32.keybd_event(0x11, 0, 2, 0)
    key(0x2E, 0.05)
    for _ in range(times):
        key(0x08, 0.01)


def place(x=None, y=None, ww=None, hh=None):
    """把客户端窗口摆到指定屏幕位置（默认左上角，留出标题栏）。"""
    w = bring()
    u32.SetWindowPos(wt.HWND(w['hwnd']), wt.HWND(-1),
                     x if x is not None else 40, y if y is not None else 40,
                     ww or w['rect'][2], hh or w['rect'][3], 0)
    time.sleep(0.4)
    return rect(w)


def log(text):
    print(text, flush=True)
