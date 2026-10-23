# -*- coding: utf-8 -*-
"""点击诊断：确认前台窗口、双击同一坐标、并枚举点击后是否出现新窗口。"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as w

u32 = ctypes.windll.user32
x = int(sys.argv[1]) if len(sys.argv) > 1 else 780
y = int(sys.argv[2]) if len(sys.argv) > 2 else 536

win = w.find_window(class_eq='Game')
h = win['hwnd']
print('game hwnd', h, 'rect', win['rect'])
u32.SetForegroundWindow(h)
time.sleep(0.5)
print('fg after SetForegroundWindow', u32.GetForegroundWindow(), 'want', h,
      'match', u32.GetForegroundWindow() == h)
u32.SetCursorPos(*[int(v) for v in (w.client_origin(h)[0] + x, w.client_origin(h)[1] + y)])
time.sleep(0.2)
print('cursor', ctypes.wintypes.POINT(), 'hwnd_under_cursor',
      u32.WindowFromPoint(wt.POINT(*[int(v) for v in (w.client_origin(h)[0] + x,
                                                      w.client_origin(h)[1] + y)])))
for i in range(2):
    w.click_client(x, y, 0.9, hwnd=h)
    print('click', i, 'fg now', u32.GetForegroundWindow())
w.shot(w.refresh(win), 'diag_after_clicks')
for row in w.enum_windows():
    if row['class'] in ('Game', '#32770') or '梦想' in row['title']:
        print('win', row)
