# -*- coding: utf-8 -*-
"""登录界面正确输入：先退格清空，再分别输入账号/密码，最后点登录。"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'server_console.log')
account = sys.argv[1] if len(sys.argv) > 1 else '910001'
password = sys.argv[2] if len(sys.argv) > 2 else '123456'

ACC_Y = 284
PWD_Y = 316


def clear_field(y, times=24):
    wt.click_client(402, y, 0.35)
    for _ in range(times):
        wt.key(0x08, 0.02)


w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.2)
w = wt.refresh(w)
h = w['hwnd']

clear_field(ACC_Y)
wt.type_digits(account, 0.1)
wt.shot(w, 'F1_account')

clear_field(PWD_Y, 12)
wt.type_digits(password, 0.1)
wt.shot(w, 'F2_password')

wt.click_client(704, 452, 2.5, hwnd=h)
wt.shot(wt.refresh(w), 'F3_login')
lines = io.open(LOG, encoding='utf-8', errors='replace').read().splitlines()
print('--- log tail ---')
for ln in lines[-10:]:
    print(ln)
