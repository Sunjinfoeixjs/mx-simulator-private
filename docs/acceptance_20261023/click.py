# -*- coding: utf-8 -*-
"""按客户区坐标点击并截图：python click.py <x> <y> [shot_name] [wait]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

x = int(sys.argv[1])
y = int(sys.argv[2])
name = sys.argv[3] if len(sys.argv) > 3 else None
wait = float(sys.argv[4]) if len(sys.argv) > 4 else 0.9

w = wt.find_window(class_eq='Game')
if not w:
    print('no Game window')
    raise SystemExit(1)
wt.bring(w)
import time
time.sleep(1.2)                       # 客户端会做窗口位移动画，等它停稳
w = wt.refresh(w)
print('rect_at_click', w['rect'], 'client_origin', wt.client_origin(w['hwnd']))
wt.click_client(x, y, wait, hwnd=w['hwnd'])
if name:
    wt.shot(wt.refresh(w), name)
print('clicked client(%d,%d)' % (x, y))
