# -*- coding: utf-8 -*-
"""在选服界面点「本地测试服」，然后点「下一步」，各截一图。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.0)
w = wt.refresh(w)
h = w['hwnd']

wt.click_client(680, 121, 1.0, hwnd=h)      # 推荐服务器 / 本地测试服
wt.shot(wt.refresh(w), 'S1_local_selected')

wt.click_client(400, 563, 2.0, hwnd=h)      # 下一步
wt.shot(wt.refresh(w), 'S2_next')
print('done')
