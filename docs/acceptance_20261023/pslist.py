# -*- coding: utf-8 -*-
"""列出与客户端/服务端相关的进程及启动时间。"""
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ps = subprocess.run(['powershell', '-NoProfile', '-Command',
                     'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,Name,CommandLine | ConvertTo-Json -Depth 3'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace')
import json
rows = json.loads(ps.stdout or '[]')
if isinstance(rows, dict):
    rows = [rows]
keys = ('mx', 'game', 'python', 'singleplayer')
for r in rows:
    name = (r.get('Name') or '').lower()
    cmd = r.get('CommandLine') or ''
    if 'mx' in name or 'game' in name or 'python' in name:
        print(r.get('ProcessId'), '<-', r.get('ParentProcessId'), r.get('CreationDate'), name)
        print('    ', cmd[:220])
