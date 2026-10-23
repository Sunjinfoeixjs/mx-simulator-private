# -*- coding: utf-8 -*-
"""带重试的真实鼠标点击：每次点击前重新置顶客户端，点后按服务端日志/截图判定。

python click_retry.py <x> <y> <shot_name> [tries]
"""
import ctypes
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

u32 = ctypes.windll.user32
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'server_console.log')

x, y = int(sys.argv[1]), int(sys.argv[2])
name = sys.argv[3] if len(sys.argv) > 3 else 'click'
tries = int(sys.argv[4]) if len(sys.argv) > 4 else 1

for i in range(tries):
    w = wt.find_window(class_eq='Game')
    h = w['hwnd']
    u32.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002)      # TOPMOST, keep pos/size
    u32.BringWindowToTop(h)
    u32.SetForegroundWindow(h)
    time.sleep(0.5)
    print('try', i, 'fg_match', u32.GetForegroundWindow() == h, 'rect', wt.refresh(w)['rect'])
    wt.click_client(x, y, 1.6, hwnd=h)
    wt.shot(wt.refresh(w), '%s_%d' % (name, i))
    if i + 1 < tries:
        time.sleep(0.8)
print('done')
