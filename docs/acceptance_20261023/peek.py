# -*- coding: utf-8 -*-
"""截取当前客户端窗口状态。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

name = sys.argv[1] if len(sys.argv) > 1 else 'peek'
w = wt.find_window(class_eq='Game') or wt.find_window(title_sub='梦想世界')
if not w:
    print('no client window')
    raise SystemExit(1)
print(w['class'], repr(w['title']), w['rect'])
wt.shot(w, name)
