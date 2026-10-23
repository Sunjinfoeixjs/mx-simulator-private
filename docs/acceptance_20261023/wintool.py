# -*- coding: utf-8 -*-
"""本轮实机验收用的最小 Win32 驱动工具（枚举/截图/点击/按键）。"""
import ctypes
import ctypes.wintypes as wt
import os
import time

from PIL import ImageGrab

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

u32.FindWindowW.restype = wt.HWND
u32.GetForegroundWindow.restype = wt.HWND
u32.GetDlgItem.restype = wt.HWND
u32.WindowFromPoint.restype = wt.HWND
u32.WindowFromPoint.argtypes = [wt.POINT]

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(HERE, 'shots')
os.makedirs(SHOT_DIR, exist_ok=True)

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


def enum_windows(visible_only=True):
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
            rows.append({'hwnd': h, 'visible': vis, 'class': cls.value,
                         'title': n.value,
                         'rect': (r.left, r.top, r.right - r.left, r.bottom - r.top)})
        return True

    u32.EnumWindows(cb, 0)
    return rows


def find_window(title_sub=None, class_eq=None, visible=True, last=True):
    hits = []
    for w in enum_windows(visible_only=False):
        if visible and not w['visible']:
            continue
        if title_sub and title_sub not in w['title']:
            continue
        if class_eq and w['class'] != class_eq:
            continue
        hits.append(w)
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def wait_window(title_sub=None, class_eq=None, timeout=30, poll=0.4):
    end = time.time() + timeout
    while time.time() < end:
        w = find_window(title_sub, class_eq)
        if w:
            return w
        time.sleep(poll)
    return None


def bring(w, topmost=True):
    h = w['hwnd']
    u32.ShowWindow(h, 9)                    # SW_RESTORE
    if topmost:
        # 验收期间保持置顶：IDE/其它窗口会盖住客户区，导致点击落到别的窗口
        ok = set_topmost(h, True)
        time.sleep(0.2)
        print('set_topmost', ok, 'now', is_topmost(h))
    u32.SetForegroundWindow(h)
    time.sleep(0.4)
    return refresh(w)


def untop(w):
    set_topmost(w['hwnd'], False)


def refresh(w):
    r = wt.RECT()
    u32.GetWindowRect(w['hwnd'], ctypes.byref(r))
    w['rect'] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return w


def shot(w=None, name='shot', pad=0):
    """截窗口（或全屏）并返回文件路径。"""
    if w is not None:
        refresh(w)
        x, y, ww, hh = w['rect']
        box = (x - pad, y - pad, x + ww + pad, y + hh + pad)
    else:
        box = None
    img = ImageGrab.grab(bbox=box)
    path = os.path.join(SHOT_DIR, '%s.png' % name)
    img.save(path)
    print('shot', path, img.size)
    return path


def click_abs(x, y, wait=0.6):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(wait)


def click(w, rx, ry, wait=0.6):
    """相对窗口左上角点击。"""
    refresh(w)
    x, y, _ww, _hh = w['rect']
    click_abs(x + rx, y + ry, wait)


def move_rel(w, rx, ry):
    refresh(w)
    x, y, _ww, _hh = w['rect']
    u32.SetCursorPos(int(x + rx), int(y + ry))
    time.sleep(0.1)


def key(vk, wait=0.08):
    u32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.04)
    u32.keybd_event(vk, 0, 2, 0)
    time.sleep(wait)


def type_digits(text, wait=0.1):
    for ch in text:
        key(0x30 + int(ch))
        time.sleep(wait)


def type_vk(vk, shift=False, wait=0.05):
    if shift:
        u32.keybd_event(0x10, 0, 0, 0)
        time.sleep(0.02)
    key(vk, wait)
    if shift:
        u32.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.02)


u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
u32.SetWindowPos.restype = wt.BOOL


def set_topmost(hwnd, on=True):
    """正确声明 argtypes 后才能置顶成功（裸传 -1 会静默失败）。"""
    insert = wt.HWND(-1) if on else wt.HWND(-2)
    ok = u32.SetWindowPos(wt.HWND(hwnd), insert, 0, 0, 0, 0,
                          SWP_NOSIZE | SWP_NOMOVE)
    return bool(ok)


def is_topmost(hwnd):
    return bool(u32.GetWindowLongW(wt.HWND(hwnd), -20) & 0x00000008)


def client_origin(hwnd=None):
    h = hwnd or find_window(class_eq='Game')['hwnd']
    pt = wt.POINT(0, 0)
    u32.ClientToScreen(h, ctypes.byref(pt))
    return pt.x, pt.y


def click_client(rx, ry, wait=0.7, hwnd=None):
    """按客户区坐标点击（自动换算屏幕坐标）。"""
    ox, oy = client_origin(hwnd)
    click_abs(ox + rx, oy + ry, wait)


def move_client_cursor(rx, ry, hwnd=None):
    ox, oy = client_origin(hwnd)
    u32.SetCursorPos(int(ox + rx), int(oy + ry))
    time.sleep(0.1)


def click_text_pixel(path=None):
    """占位：本轮不依赖 OCR。"""
    raise NotImplementedError
