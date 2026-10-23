# -*- coding: utf-8 -*-
"""探针：确认当前会话是否有可操作的 Windows 图形桌面。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

print('python', sys.version)
print('admin', bool(ctypes.windll.shell32.IsUserAnAdmin()))

sid = wt.DWORD()
k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid))
print('session_id', sid.value)
print('screen', u32.GetSystemMetrics(0), 'x', u32.GetSystemMetrics(1))
print('desktop_hwnd', u32.GetDesktopWindow())
print('foreground', u32.GetForegroundWindow())

rows = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(h, lp):
    n = ctypes.create_unicode_buffer(256)
    cls = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(h, n, 256)
    u32.GetClassNameW(h, cls, 256)
    if n.value:
        r = wt.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        rows.append((h, bool(u32.IsWindowVisible(h)), cls.value, n.value,
                     (r.left, r.top, r.right - r.left, r.bottom - r.top)))
    return True


u32.EnumWindows(cb, 0)
print('window_count', len(rows))
for x in rows:
    print('  ', x)

try:
    from PIL import ImageGrab
    img = ImageGrab.grab()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_probe_shot.png')
    img.save(out)
    print('shot', out, img.size, 'extrema', img.convert('L').getextrema())
except Exception as exc:  # noqa
    print('shot_failed', repr(exc))
