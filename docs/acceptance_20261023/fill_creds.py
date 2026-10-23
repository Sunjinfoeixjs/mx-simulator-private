# -*- coding: utf-8 -*-
"""在登录界面填账号与密码（每步截图，不点登录）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

account = sys.argv[1] if len(sys.argv) > 1 else '910001'
password = sys.argv[2] if len(sys.argv) > 2 else '123456'

w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.0)
w = wt.refresh(w)
h = w['hwnd']

wt.click_client(402, 282, 0.5, hwnd=h)
wt.type_digits(account, 0.1)
wt.shot(wt.refresh(w), 'C1_account')

wt.click_client(402, 314, 0.6, hwnd=h)
wt.type_digits(password, 0.1)
wt.shot(wt.refresh(w), 'C2_password')
print('filled')
