# -*- coding: utf-8 -*-
"""查询某个 hwnd 的窗口信息，并列出当前前台窗口。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as w

u32 = ctypes.windll.user32
targets = [int(a, 16) for a in sys.argv[1:]] or [u32.GetForegroundWindow()]
for h in targets:
    t = ctypes.create_unicode_buffer(512)
    c = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(h, t, 512)
    u32.GetClassNameW(h, c, 256)
    r = wt.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    print('hwnd 0x%X pid=%d class=%r title=%r rect=%s visible=%s'
          % (h, pid.value, c.value, t.value, (r.left, r.top, r.right - r.left,
                                             r.bottom - r.top),
             bool(u32.IsWindowVisible(h))))
print('foreground', hex(u32.GetForegroundWindow()))
