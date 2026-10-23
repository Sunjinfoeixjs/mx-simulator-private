# -*- coding: utf-8 -*-
"""run2 准备：清场 → 起服务端(--web) + 本地 HTTP → 拉客户端 → 登录界面截图。"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
CLIENT = r'D:\mxsj2013\client'
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save')
LOG = os.path.join(HERE, 'server.log')
PORT = 12239
WEB = 8099


def kill_tree(pid):
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                   capture_output=True, text=True, encoding='mbcs', errors='replace')


# ① 上一轮残留
for img in ('mxmain.dll', 'mx_noadmin.exe', 'mx.exe'):
    subprocess.run(['taskkill', '/F', '/T', '/IM', img], capture_output=True,
                   text=True, encoding='mbcs', errors='replace')
time.sleep(1.5)

# ② 清隔离存档（保留目录）
os.makedirs(SAVE, exist_ok=True)
for fn in os.listdir(SAVE):
    p = os.path.join(SAVE, fn)
    if os.path.isfile(p):
        os.remove(p)
print('save 已清空:', os.listdir(SAVE))

# ③ 清账号记忆，避免自动登录旧账号
users = os.path.join(CLIENT, 'users')
os.makedirs(users, exist_ok=True)
open(os.path.join(users, 'usr.lst'), 'wb').write(b'')
for fn in ('role.ini', '1.ini'):
    p = os.path.join(users, fn)
    if os.path.isfile(p):
        os.remove(p)
mx = os.path.join(CLIENT, 'mx.ini')
raw = open(mx, 'rb').read().decode('gbk', 'replace')
out = []
for ln in raw.replace('\r\n', '\n').split('\n'):
    low = ln.strip().lower()
    if low.startswith('accountlist='):
        out.append('accountlist=')
    elif low.startswith('refreshserver='):
        out.append('refreshserver=4102444800.000000')
    else:
        out.append(ln)
open(mx, 'wb').write('\r\n'.join(out).encode('gbk'))
print('账号记忆已清空')

# ④ 服务端（GM 面板 8099，仅本机）
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
slog = open(LOG, 'wb')
sp = subprocess.Popen([PY, '-u', os.path.join(SIM, 'm4_server.py'),
                       '--host', '127.0.0.1', '--port', str(PORT),
                       '--save-dir', SAVE, '--no-encrypt', '--fresh',
                       '--web', '--web-port', str(WEB)],
                      cwd=SIM, stdout=slog, stderr=subprocess.STDOUT, env=env,
                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
open(os.path.join(HERE, 'server.pid'), 'w').write(str(sp.pid))
print('server pid', sp.pid)

# ⑤ 本地 HTTP（客户端 update.ini 指向 8080）
net = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                     encoding='mbcs', errors='replace').stdout
if ':8080 ' not in net:
    hlog = open(os.path.join(HERE, 'http.log'), 'wb')
    hp = subprocess.Popen([PY, '-u', os.path.join(SIM, 'local_http.py'), '--port', '8080'],
                          cwd=SIM, stdout=hlog, stderr=subprocess.STDOUT, env=env,
                          creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    open(os.path.join(HERE, 'http.pid'), 'w').write(str(hp.pid))
    print('local_http pid', hp.pid)
else:
    print('8080 已在监听')

time.sleep(3.5)

# ⑥ 就绪探针
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:%d/api/state' % WEB, timeout=5) as r:
        st = json.loads(r.read().decode('utf-8'))
    print('GM api ok: sessions=%s maps=%s' % (st.get('sessions'), len(st.get('maps') or [])))
except Exception as e:
    print('GM api FAIL', repr(e))

# ⑦ 拉客户端
p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
open(os.path.join(HERE, 'client.pid'), 'w').write(str(p.pid))
print('client pid', p.pid)
w = ui.wait_client(90)
print('Game 窗口', w['rect'] if w else None)
time.sleep(5)
ui.place(30, 20)
time.sleep(0.5)
ui.shot('A0_login')
with open(os.path.join(HERE, 'state.json'), 'w') as f:
    json.dump({'server_pid': sp.pid, 'web': WEB, 'port': PORT, 'save': SAVE}, f)
print('prep done')
