# -*- coding: utf-8 -*-
"""发送按键：python key.py <vk_hex> [shot_name]

例：python key.py 1B  -> Esc
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

vk = int(sys.argv[1], 16)
name = sys.argv[2] if len(sys.argv) > 2 else None

w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.0)
wt.key(vk, 0.3)
if name:
    wt.shot(wt.refresh(w), name)
print('sent vk 0x%02X' % vk)
