# -*- coding: utf-8 -*-
"""全屏截图 + 打印客户端窗口矩形，便于核对点击坐标。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

name = sys.argv[1] if len(sys.argv) > 1 else 'full'
w = wt.find_window(class_eq='Game')
print('window', w['rect'] if w else None)
wt.shot(None, name)
