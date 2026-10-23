# -*- coding: utf-8 -*-
"""分步登录：每步截图，便于核对输入框状态。

python login_stepwise.py <account> <password>
"""
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
print('rect', w['rect'], 'client', wt.client_origin(h))

step = sys.argv[3] if len(sys.argv) > 3 else 'all'

if step in ('all', 'acc'):
    wt.click_client(397, 282, 0.5, hwnd=h)
    wt.type_digits(account, 0.12)
    wt.shot(w, 'L1_account')

if step in ('all', 'pwd'):
    wt.click_client(397, 314, 0.5, hwnd=h)
    wt.type_digits(password, 0.12)
    wt.shot(w, 'L2_password')

if step in ('all', 'login'):
    wt.click_client(704, 452, 2.5, hwnd=h)
    wt.shot(w, 'L3_login')
