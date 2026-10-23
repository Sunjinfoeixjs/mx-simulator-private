# -*- coding: utf-8 -*-
"""战斗指令自动尝试：触发遇敌 → 轮询 in_war → 连点敌方候选坐标直到上行指令出现。

为什么这么做：实机验证发现战斗界面吃点击的时机很窄（指令阶段内、且要窗口激活），
人工一步一截图会错过 30s 回合窗口。这里用 GM 面板的 in_war 做同步信号。
"""
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'server.log')
WEB = 'http://127.0.0.1:8099/'


def post(path, body):
    req = urllib.request.Request(WEB + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def state():
    return json.loads(urllib.request.urlopen(WEB + 'api/state', timeout=5).read())


def logtail(n=200):
    with io.open(LOG, encoding='utf-8', errors='replace') as f:
        return f.read().splitlines()[-n:]


def cmd_seen():
    for ln in logtail(60):
        if 'opcode=16' in ln or '普攻' in ln and '未下达' not in ln:
            if 'opcode=160' in ln or 'opcode=161' in ln or 'opcode=162' in ln:
                return ln
    return None


post('api/encounter', {'every': 1})
if state().get('in_war'):
    post('api/battle/win', {})
    time.sleep(1.2)
post('api/map', {'mapid': 1005, 'x': 80, 'y': 60})
time.sleep(1.8)

# 触发一次遇敌
ui.click(520, 300, 1.0)
t0 = time.time()
while time.time() - t0 < 8:
    if state().get('in_war'):
        break
    time.sleep(0.3)
print('in_war', state().get('in_war'), flush=True)
time.sleep(1.6)

cands = [(227, 189), (230, 215), (240, 200), (212, 175), (250, 230), (225, 195)]
hit = None
for i in range(24):
    x, y = cands[i % len(cands)]
    ui.click(x, y, 0.45)
    time.sleep(0.35)
    s = cmd_seen()
    if s:
        hit = s
        print('CMD HIT after %d clicks: %s' % (i + 1, s), flush=True)
        break
print('hit', hit, flush=True)
ui.shot('B_auto_attack')
print('in_war now', state().get('in_war'), 'exp', state().get('exp'), flush=True)
