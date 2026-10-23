# -*- coding: utf-8 -*-
"""按端口清场：杀掉占用 12239 / 8099 / 127.0.0.1:8080 的进程，再确认端口已释放。

★ 为什么按端口而不是按映像名：上一轮出现过「同名进程被 taskkill /T 打漏」，
  以及 py 启动器与真实解释器两个 PID 的情况；端口占用是唯一可靠判据。
"""
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PORTS = (12239, 8099)
HTTP_PORT = 8080


def listeners():
    out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                         encoding='mbcs', errors='replace').stdout
    hits = {}
    for ln in out.splitlines():
        m = re.match(r'\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)', ln)
        if not m:
            continue
        addr, port, pid = m.group(1), int(m.group(2)), int(m.group(3))
        if port in PORTS or (port == HTTP_PORT and addr.startswith('127.')):
            hits.setdefault(pid, []).append(port)
    return hits


for _ in range(3):
    hits = listeners()
    if not hits:
        break
    for pid, ports in hits.items():
        print('kill', pid, ports)
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                       capture_output=True, text=True, encoding='mbcs', errors='replace')
    time.sleep(1.5)

time.sleep(0.5)
left = listeners()
print('left', left)
sys.exit(0 if not left else 2)
