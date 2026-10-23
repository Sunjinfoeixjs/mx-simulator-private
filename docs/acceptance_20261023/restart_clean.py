# -*- coding: utf-8 -*-
"""干净重启：停客户端 → 清隔离存档 → 重启多会话服 → 清账号记忆 → 拉客户端到登录界面。"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import wintool as wt

PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
CLIENT = r'D:\mxsj2013\client'
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save_iso')
ACCOUNT = os.environ.get('ACC_ACCOUNT', '910001')

# ① 停客户端
for img in ('mxmain.dll', 'mx_noadmin.exe', 'mx.bin', 'mx.exe'):
    subprocess.run(['taskkill', '/F', '/T', '/IM', img], capture_output=True,
                   text=True, encoding='mbcs', errors='replace')
time.sleep(2.0)

# ② 停旧服务端，清存档
pid_file = os.path.join(HERE, 'server_pid.txt')
if os.path.isfile(pid_file):
    pid = open(pid_file).read().strip()
    if pid:
        subprocess.run(['taskkill', '/F', '/T', '/PID', pid], capture_output=True,
                       text=True, encoding='mbcs', errors='replace')
    os.remove(pid_file)
for fn in os.listdir(SAVE):
    try:
        os.remove(os.path.join(SAVE, fn))
    except OSError:
        pass
print('save_iso 已清空', os.listdir(SAVE))

# ③ 清账号记忆（否则客户端会拿旧账号自动登录）
users = os.path.join(CLIENT, 'users')
bakdir = os.path.join(HERE, 'client_users_bak')
os.makedirs(bakdir, exist_ok=True)
for fn in ('usr.lst', 'role.ini'):
    src = os.path.join(users, fn)
    if os.path.isfile(src) and not os.path.isfile(os.path.join(bakdir, fn)):
        shutil.copy2(src, os.path.join(bakdir, fn))
open(os.path.join(users, 'usr.lst'), 'wb').write(b'')
print('users/usr.lst 已清空（原文件已备份到 client_users_bak）')

mx = os.path.join(CLIENT, 'mx.ini')
raw = open(mx, 'rb').read().decode('gbk')
lines = raw.replace('\r\n', '\n').split('\n')
out = []
for ln in lines:
    if ln.strip().lower().startswith('accountlist='):
        out.append('accountlist=')
    elif ln.strip().lower().startswith('refreshserver='):
        out.append('refreshserver=4102444800.000000')
    else:
        out.append(ln)
open(mx, 'wb').write('\r\n'.join(out).encode('gbk'))

# ④ 起多会话服
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
slog = open(os.path.join(HERE, 'server_console.log'), 'wb')
sp = subprocess.Popen([PY, '-u', os.path.join(SIM, 'm4_server.py'),
                       '--host', '127.0.0.1', '--port', '12239',
                       '--save-dir', SAVE, '--no-encrypt', '--fresh'],
                      cwd=SIM, stdout=slog, stderr=subprocess.STDOUT, env=env,
                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
open(os.path.join(HERE, 'server_pid.txt'), 'w').write(str(sp.pid))
print('多会话服 pid', sp.pid)

# ⑤ HTTP 劫持（若已停则重启）
net = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                     encoding='mbcs', errors='replace').stdout
if ':8080 ' not in net:
    hlog = open(os.path.join(HERE, 'http_console.log'), 'wb')
    hp = subprocess.Popen([PY, '-u', os.path.join(SIM, 'local_http.py'), '--port', '8080'],
                          cwd=SIM, stdout=hlog, stderr=subprocess.STDOUT, env=env,
                          creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    open(os.path.join(HERE, 'http_pid.txt'), 'w').write(str(hp.pid))
    print('local_http 已启动 pid', hp.pid)
else:
    print('local_http 仍在监听 8080')

time.sleep(3.5)

# ⑥ 拉客户端
p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
print('客户端 pid', p.pid)
w = wt.wait_window(class_eq='Game', timeout=60)
print('Game 窗口', w['rect'] if w else None)
time.sleep(6)
w = wt.find_window(class_eq='Game')
wt.bring(w)
time.sleep(1.0)
wt.shot(wt.refresh(w), 'login_00')
print('已截图 login_00')
