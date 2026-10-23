# -*- coding: utf-8 -*-
"""起服务端 + 跑协议层 30 分钟稳定性，收在同一个常驻进程里（避免后台进程被连带杀掉）。"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def port_open():
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 12239))
        return True
    except OSError:
        return False
    finally:
        s.close()


subprocess.run([sys.executable, 'clean_ports.py'], cwd=HERE, stdout=subprocess.DEVNULL)
srv = subprocess.Popen([sys.executable, '-u', 'boot.py'], cwd=HERE)
print('boot pid', srv.pid, flush=True)
for _ in range(40):
    if port_open():
        break
    time.sleep(0.5)
else:
    print('FATAL server not up', flush=True)
    sys.exit(2)
print('server up', flush=True)

import soak_proto  # noqa: E402

rc = soak_proto.main()
print('soak_proto rc', rc, flush=True)
