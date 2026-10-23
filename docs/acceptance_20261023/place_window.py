# -*- coding: utf-8 -*-
"""把客户端窗口移到可见位置并置前，然后截窗口图 + 全屏图。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wtool

u32 = ctypes.windll.user32

X = int(sys.argv[2]) if len(sys.argv) > 2 else 20
Y = int(sys.argv[3]) if len(sys.argv) > 3 else 10
name = sys.argv[1] if len(sys.argv) > 1 else 'placed'

w = wtool.find_window(class_eq='Game')
if not w:
    print('no Game window')
    raise SystemExit(1)
h = w['hwnd']
u32.SetWindowPos(h, -1, X, Y, 806, 629, 0x0040)      # HWND_TOPMOST + MOVE + SHOW
u32.ShowWindow(h, 9)
u32.SetForegroundWindow(h)
time.sleep(0.8)
w = wtool.refresh(w)
print('window now', w['rect'])
try:
    cr = wt.RECT()
    u32.GetClientRect(h, ctypes.byref(cr))
    print('client size', cr.right - cr.left, cr.bottom - cr.top)
    pt = wt.POINT(0, 0)
    u32.ClientToScreen(h, ctypes.byref(pt))
    print('client origin(screen)', pt.x, pt.y)
except Exception as exc:  # noqa
    print('client info failed', exc)
wtool.shot(w, name)
wtool.shot(None, name + '_full')
