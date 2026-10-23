# -*- coding: utf-8 -*-
"""用 PostMessage 向客户端窗口投递鼠标点击（绕开窗口 z 序/前台限制）。

python pmclick.py <x> <y> [shot_name] [wait]
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as w

u32 = ctypes.windll.user32
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
MK_LBUTTON = 0x0001

x, y = int(sys.argv[1]), int(sys.argv[2])
name = sys.argv[3] if len(sys.argv) > 3 else None
wait = float(sys.argv[4]) if len(sys.argv) > 4 else 0.9

win = w.find_window(class_eq='Game')
h = win['hwnd']
lp = (y << 16) | (x & 0xFFFF)
u32.PostMessageW(h, WM_MOUSEMOVE, 0, lp)
time.sleep(0.05)
u32.PostMessageW(h, WM_LBUTTONDOWN, MK_LBUTTON, lp)
time.sleep(0.08)
u32.PostMessageW(h, WM_LBUTTONUP, 0, lp)
time.sleep(wait)
if name:
    w.shot(w.refresh(win), name)
print('post click', x, y)
