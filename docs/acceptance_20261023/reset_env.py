# -*- coding: utf-8 -*-
"""本轮验收环境复位：清理旧进程 → 隔离存档 → 多会话服务端 + 本地 HTTP 劫持。

用法:
    python reset_env.py [--keep-save]
"""
import os
import shutil
import subprocess
import sys
import time

PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
CLIENT = r'D:\mxsj2013\client'
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save_iso')
PORT = '12239'
HTTP_PORT = '8080'
KEEP = '--keep-save' in sys.argv

os.makedirs(SAVE, exist_ok=True)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def kill_image(names):
    ps = subprocess.run(['taskkill', '/F', '/T', '/IM', names],
                        capture_output=True, text=True, encoding='mbcs', errors='replace')
    print('kill', names, '->', ps.returncode)


# ① 收掉旧客户端与旧服务端
for img in ('mxmain.dll', 'mx_noadmin.exe', 'mx.bin', 'mx.exe', 'mx_run.exe'):
    kill_image(img)
for fn in ('server_pid.txt',):
    p = os.path.join(HERE, fn)
    if os.path.isfile(p):
        pid = open(p).read().strip()
        if pid:
            subprocess.run(['taskkill', '/F', '/T', '/PID', pid],
                           capture_output=True, text=True, encoding='mbcs',
                           errors='replace')
            print('kill server pid', pid)
        os.remove(p)
time.sleep(1.5)

# ② 客户端 mx.ini：清空记住的账号，保证本轮是真实手工登录；refreshserver 推向远期
mx = os.path.join(CLIENT, 'mx.ini')
bak = os.path.join(HERE, 'mx.ini.bak_before_acceptance')
if not os.path.isfile(bak):
    shutil.copy2(mx, bak)
    print('mx.ini 已备份 ->', bak)
raw = open(mx, 'rb').read().decode('gbk')
lines, out = raw.replace('\r\n', '\n').split('\n'), []
seen_login = False
for ln in lines:
    s = ln.strip().lower()
    if s == '[login]':
        seen_login = True
        out.append(ln)
        continue
    if s.startswith('[') and s.endswith(']'):
        seen_login = False
    if s.startswith('accountlist='):
        out.append('accountlist=')
        continue
    if s.startswith('refreshserver='):
        out.append('refreshserver=4102444800.000000')
        continue
    out.append(ln)
open(mx, 'wb').write('\r\n'.join(out).encode('gbk'))
print('mx.ini: accountlist 已清空, refreshserver→远期')

# ③ 启动多会话服务端
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
sargs = [PY, '-u', os.path.join(SIM, 'm4_server.py'),
         '--host', '127.0.0.1', '--port', PORT,
         '--save-dir', SAVE, '--no-encrypt']
if not KEEP:
    sargs.append('--fresh')
slog = open(os.path.join(HERE, 'server_console.log'), 'wb')
sp = subprocess.Popen(sargs, cwd=SIM, stdout=slog, stderr=subprocess.STDOUT, env=env,
                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
print('multi-session server pid', sp.pid)
open(os.path.join(HERE, 'server_pid.txt'), 'w').write(str(sp.pid))

# ④ 启动本地 HTTP 劫持（客户端刷新列表/内嵌页）
hlog = open(os.path.join(HERE, 'http_console.log'), 'wb')
hp = subprocess.Popen([PY, '-u', os.path.join(SIM, 'local_http.py'),
                       '--port', HTTP_PORT], cwd=SIM, stdout=hlog,
                      stderr=subprocess.STDOUT, env=env,
                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
print('local_http pid', hp.pid)
open(os.path.join(HERE, 'http_pid.txt'), 'w').write(str(hp.pid))

time.sleep(4)
net = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                     encoding='mbcs', errors='replace').stdout
for port in (PORT, HTTP_PORT):
    hits = [ln.strip() for ln in net.splitlines()
            if ':%s ' % port in ln and 'LISTENING' in ln]
    print('port', port, hits or 'NOT LISTENING')
print('save_dir', SAVE, sorted(os.listdir(SAVE)))
