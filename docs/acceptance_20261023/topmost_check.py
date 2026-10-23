# -*- coding: utf-8 -*-
"""检查客户端与 IDE 窗口的 TOPMOST 状态和 z 序，验证置顶是否生效。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as w

u32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

game = w.find_window(class_eq='Game')
u32.SetWindowPos(game['hwnd'], -1, 0, 0, 0, 0, 0x0003)    # TOPMOST + NOSIZE|NOMOVE
time.sleep(0.5)
print('game topmost bit', bool(u32.GetWindowLongW(game['hwnd'], GWL_EXSTYLE) & WS_EX_TOPMOST))
for row in w.enum_windows():
    ex = u32.GetWindowLongW(row['hwnd'], GWL_EXSTYLE)
    if row['class'] in ('Game', 'Chrome_WidgetWin_1'):
        print('%-28s topmost=%-5s %r' % (row['class'],
                                         bool(ex & WS_EX_TOPMOST), row['title'][:30]))
w.shot(None, 'topmost_check')
