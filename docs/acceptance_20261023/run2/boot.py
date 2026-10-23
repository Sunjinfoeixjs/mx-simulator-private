# -*- coding: utf-8 -*-
"""常驻引导：起 m4_server(--web) + local_http(8080)，并把 PID 写盘。

放在后台任务里跑；两个子进程随本进程存活（本进程不退出，子进程就不会被回收）。
"""
import json
import os
import subprocess
import sys
import time

PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save')
os.makedirs(SAVE, exist_ok=True)

env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
srv_log = open(os.path.join(HERE, 'server.log'), 'wb')
srv = subprocess.Popen([PY, '-u', os.path.join(SIM, 'm4_server.py'),
                        '--host', '127.0.0.1', '--port', '12239',
                        '--save-dir', SAVE, '--no-encrypt',
                        '--web', '--web-port', '8099'],
                       cwd=SIM, stdout=srv_log, stderr=subprocess.STDOUT, env=env)
print('server', srv.pid, flush=True)

time.sleep(2.5)
http_log = open(os.path.join(HERE, 'http.log'), 'wb')
http = subprocess.Popen([PY, '-u', os.path.join(SIM, 'local_http.py'), '--port', '8080'],
                        cwd=SIM, stdout=http_log, stderr=subprocess.STDOUT, env=env)
print('http', http.pid, flush=True)

json.dump({'server': srv.pid, 'http': http.pid}, open(os.path.join(HERE, 'boot.json'), 'w'))

while True:
    if srv.poll() is not None:
        print('SERVER EXITED', srv.returncode, flush=True)
        break
    time.sleep(5)
