# -*- coding: utf-8 -*-
"""常驻客户端驱动器：持有 mx_noadmin.exe，按 cmd.jsonl 逐行执行指令。

为什么需要它：客户端的生命周期必须独立于单次工具调用 —— 每次调用结束，
子进程会被回收；而实机验收要连续操作窗口几分钟到几十分钟。

指令（每行一个 JSON）：
  {"op":"shot","name":"A1"}
  {"op":"click","x":402,"y":284,"wait":0.6}
  {"op":"dclick","x":360,"y":250}
  {"op":"type","text":"acc01"}
  {"op":"key","vk":13}
  {"op":"combo","keys":["alt","a"]}
  {"op":"wait","sec":2}
  {"op":"bring"} / {"op":"place","x":30,"y":20}
  {"op":"windows"} / {"op":"info"} / {"op":"ping"} / {"op":"quit"}
结果写 res.jsonl（同样的行号对应）。
"""
import json
import os
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import uinput as ui

CLIENT = r'D:\mxsj2013\client'
HERE = os.path.dirname(os.path.abspath(__file__))
CMD = os.path.join(HERE, 'cmd.jsonl')
RES = os.path.join(HERE, 'res.jsonl')
LOG = os.path.join(HERE, 'agent.log')


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def start_client():
    p = subprocess.Popen([os.path.join(CLIENT, 'mx_noadmin.exe')], cwd=CLIENT)
    log('client pid %d' % p.pid)
    return p


def handle(c):
    op = c.get('op')
    if op == 'shot':
        return {'path': ui.shot(c.get('name', 'shot'), int(c.get('pad', 0)))}
    if op == 'click':
        ui.click(c['x'], c['y'], float(c.get('wait', 0.8)))
        return {'rect': ui.rect(ui.client_win())}
    if op == 'rclick':
        ui.rclick(c['x'], c['y'], float(c.get('wait', 0.8)))
        return {'rect': ui.rect(ui.client_win())}
    if op == 'dclick':
        ui.dclick(c['x'], c['y'], float(c.get('wait', 1.2)))
        return {'rect': ui.rect(ui.client_win())}
    if op == 'type':
        ui.type_text(c['text'], float(c.get('gap', 0.06)))
        return {'typed': c['text']}
    if op == 'key':
        ui.key(int(c['vk']), float(c.get('wait', 0.1)))
        return {'vk': c['vk']}
    if op == 'combo':
        ui.combo(*c['keys'])
        return {'keys': c['keys']}
    if op == 'wait':
        time.sleep(float(c.get('sec', 1)))
        return {'slept': c.get('sec', 1)}
    if op == 'bring':
        return {'rect': ui.bring()['rect']}
    if op == 'place':
        return {'rect': ui.place(c.get('x'), c.get('y'), c.get('w'), c.get('h'))}
    if op == 'windows':
        return {'wins': [{'class': w['class'], 'title': w['title'], 'rect': w['rect']}
                         for w in ui.windows() if w['class'] in ('Game', '#32770')]}
    if op == 'info':
        w = ui.client_win()
        return {'alive': bool(w), 'rect': ui.rect(w)['rect'] if w else None,
                'origin': ui.origin(w) if w else None}
    if op == 'quit':
        return {'bye': True}
    return {'error': 'unknown op %r' % op}


def main():
    open(CMD, 'a').close()
    open(RES, 'w').close()
    pos0 = os.path.getsize(CMD)   # 跳过历史指令，避免重启后重放
    w0 = ui.client_win()
    p = None
    if w0:
        log('attach existing window %s' % str(w0['rect']))
    else:
        p = start_client()
    w = ui.wait_client(60)
    log('window %s' % str(w['rect'] if w else None))
    pos = pos0
    while True:
        if p is not None and p.poll() is not None:
            log('CLIENT EXITED %s' % p.returncode)
        try:
            size = os.path.getsize(CMD)
            if size < pos:                 # 文件被截断重写
                pos = 0
            if size > pos:
                with open(CMD, 'r', encoding='utf-8') as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                for ln in chunk.splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        c = json.loads(ln)
                    except Exception as e:
                        log('BAD CMD %r %r' % (ln, e))
                        continue
                    try:
                        r = handle(c)
                        r['op'] = c.get('op')
                    except Exception as e:
                        r = {'op': c.get('op'), 'error': repr(e),
                             'tb': traceback.format_exc()[-600:]}
                    log('CMD %s -> %s' % (ln, json.dumps(r, ensure_ascii=False)[:300]))
                    with open(RES, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(r, ensure_ascii=False) + '\n')
                    if c.get('op') == 'quit':
                        return
        except Exception as e:
            log('LOOP ERR %r' % e)
        time.sleep(0.15)


if __name__ == '__main__':
    main()
