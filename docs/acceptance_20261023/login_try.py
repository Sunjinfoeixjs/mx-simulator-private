# -*- coding: utf-8 -*-
"""完整登录尝试：填账号→填密码→截图→点登录→连拍 4 帧 + 打印新日志。"""
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
tag = sys.argv[3] if len(sys.argv) > 3 else 'T'

def logtail(n=12):
    try:
        lines = io.open(LOG, encoding='utf-8', errors='replace').read().splitlines()
    except OSError:
        return []
    return lines[-n:]

w = wt.find_window(class_eq='Game')
h = w['hwnd']
wt.bring(w)
time.sleep(1.5)

# 清空两个输入框后重新输入（先点账号框，Ctrl+A 删除）
u32 = wt.u32
for y in (282, 314):
    wt.click_client(397, y, 0.3, hwnd=h)
    u32.keybd_event(0x11, 0, 0, 0)          # Ctrl
    wt.key(0x41, 0.05)                      # A
    u32.keybd_event(0x11, 0, 2, 0)
    wt.key(0x2E, 0.05)                      # Delete
wt.click_client(397, 282, 0.3, hwnd=h)
wt.type_digits(account, 0.12)
wt.click_client(397, 314, 0.3, hwnd=h)
wt.type_digits(password, 0.12)
time.sleep(0.5)
wt.shot(wt.refresh(w), tag + '_filled')

wt.click_client(704, 452, 1.0, hwnd=h)
for i in range(4):
    wt.shot(wt.refresh(w), '%s_after_%d' % (tag, i))
    time.sleep(1.0)
print('--- log tail ---')
for ln in logtail(14):
    print(ln)
