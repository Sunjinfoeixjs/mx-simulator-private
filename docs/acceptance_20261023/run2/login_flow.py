# -*- coding: utf-8 -*-
"""一键走完「公告 → 开始游戏 → 选服 → 登录 → 选角 → 进世界」，每步截图。

坐标都是 800x600 客户区实测值（窗口 806x629，客户区左上角偏移 3,26）。
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'server.log')
ACC = sys.argv[1] if len(sys.argv) > 1 else None


def logtail(n=8):
    try:
        return io.open(LOG, encoding='utf-8', errors='replace').read().splitlines()[-n:]
    except OSError:
        return []


w = ui.wait_client(30)
if not w:
    print('NO WINDOW')
    raise SystemExit(1)
ui.place(26, 26)
time.sleep(1.0)
ui.shot('relogin_0_start')

# ① 公告弹窗的「确定」（如果有）
ui.click(407, 336, 1.0)
ui.shot('relogin_1_notice')

# ② 开始游戏
ui.click(722, 259, 3.0)
ui.shot('relogin_2_server')

# ③ 选服：回车 = 下一步
ui.key(13, 3.5)
ui.shot('relogin_3_login')

# ④ 登录：密码框多半已填；直接点「登录」
if ACC:
    ui.click(397, 274, 0.5)          # 账号框
    ui.combo('ctrl', 'a')
    ui.key(46, 0.2)
    ui.type_text(ACC)
ui.click(717, 449, 4.0)
ui.shot('relogin_4_role')

# ⑤ 选角：进游戏
ui.click(697, 449, 5.0)
ui.shot('relogin_5_world')
for ln in logtail(10):
    print(ln, flush=True)
