# -*- coding: utf-8 -*-
"""A 项：在登录界面手工输入账号/密码并登录。

python step_login.py <account> <password>
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wintool as wt

account = sys.argv[1] if len(sys.argv) > 1 else '910001'
password = sys.argv[2] if len(sys.argv) > 2 else '123456'

u32 = ctypes.windll.user32
w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.2)
w = wt.refresh(w)
ox, oy = wt.client_origin(w['hwnd'])
print('client_origin', ox, oy)

# 账号框
wt.click_client(397, 281, 0.4, hwnd=w['hwnd'])
wt.type_digits(account, 0.12)
# 密码框
wt.click_client(397, 313, 0.4, hwnd=w['hwnd'])
wt.type_digits(password, 0.12)
time.sleep(0.4)
wt.shot(wt.refresh(w), 'login_03_filled')

# 登录
wt.click_client(703, 450, 3.0, hwnd=w['hwnd'])
wt.shot(wt.refresh(w), 'login_04_after_click')
print('typed', account, '/', password)
