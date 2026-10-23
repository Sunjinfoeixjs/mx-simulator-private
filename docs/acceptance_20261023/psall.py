# -*- coding: utf-8 -*-
"""列出所有可疑进程（脚本/自动化/终端）。"""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ps = subprocess.run(['powershell', '-NoProfile', '-Command',
                     'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Depth 3'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace')
rows = json.loads(ps.stdout or '[]')
if isinstance(rows, dict):
    rows = [rows]
needles = ('mx', 'script', 'automat', 'drive', 'accept', 'playwright', 'node',
           'py.exe', 'wscript', 'cscript', 'powershell')
skip = ('CodeBuddy', 'codebuddy', 'svchost', 'RuntimeBroker', 'SearchApp')
for r in rows:
    cmd = (r.get('CommandLine') or '')
    name = (r.get('Name') or '')
    low = (cmd + ' ' + name).lower()
    if any(n in low for n in needles) and not any(s in cmd for s in skip):
        print('%-6s <- %-6s %-30s' % (r.get('ProcessId'), r.get('ParentProcessId'), name))
        print('    ', cmd[:300])
