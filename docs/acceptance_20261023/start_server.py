# -*- coding: utf-8 -*-
"""启动隔离模拟服（不拉客户端），输出重定向到 server_console.log。"""
import os
import subprocess
import sys

PY = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
SIM = r'C:\Users\Administrator\MX\服务端源码\tests\sim'
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE = os.path.join(HERE, 'save_iso')
PORT = os.environ.get('ACC_PORT', '12239')
OUT = os.path.join(HERE, 'server_console.log')

os.makedirs(SAVE, exist_ok=True)

args = [PY, '-u', os.path.join(SIM, 'singleplayer.py'),
        '--no-client', '--port', PORT, '--save-dir', SAVE,
        '--no-encrypt', '--http-port', '8080']
if os.environ.get('ACC_FRESH', '1') == '1':
    args.append('--fresh')
if os.environ.get('ACC_NO_ENCOUNTER') == '1':
    args.append('--no-encounter')

env = dict(os.environ)
env['PYTHONIOENCODING'] = 'utf-8'
env['PYTHONUTF8'] = '1'

logf = open(OUT, 'ab')
p = subprocess.Popen(args, cwd=SIM, stdout=logf, stderr=subprocess.STDOUT,
                     env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
print('server_pid %d' % p.pid)
print('cmd %s' % ' '.join(args))
print('console_log %s' % OUT)
with open(os.path.join(HERE, 'server_pid.txt'), 'w', encoding='ascii') as f:
    f.write(str(p.pid))
