# -*- coding: utf-8 -*-
"""把客户端窗口移动到指定位置，并观察 3 秒内是否被客户端自己挪走。"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wtool

u32 = ctypes.windll.user32
X, Y = 250, 60
if len(sys.argv) > 2:
    X, Y = int(sys.argv[1]), int(sys.argv[2])

w = wtool.find_window(class_eq='Game')
h = w['hwnd']
for attempt in range(3):
    u32.MoveWindow(h, X, Y, 806, 629, True)
    time.sleep(0.4)
    w = wtool.refresh(w)
    print('attempt', attempt, 'rect', w['rect'])
    if w['rect'][:2] == (X - 4, Y - 27) or abs(w['rect'][0] - X) < 12:
        break
for i in range(4):
    time.sleep(0.7)
    w = wtool.refresh(w)
    print('t=%.1fs rect' % (0.7 * (i + 1)), w['rect'])
wtool.bring(w)
wtool.shot(wtool.refresh(w), 'moved')
