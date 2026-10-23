# -*- coding: utf-8 -*-
"""真机实验半自动 harness：op100/op101 新分支验证（2026-10-19 手动实验的可重复版）。

把「起内核 → 改配置 → 用户拉客户端 → 点村长 → 抓帧 → 截图 → 写报告」这条链
固化成一条命令；能自动判的自己判，必须人点的（UAC/登录/对话框）打印指引。

流程：
  ① 调试台没起就起一个（debugger.py --no-client --no-encounter，子进程）；
     已在跑就复用（复用不杀，自己起的结束时才收，--keep 可保留）。
  ② 客户端配置指向内核端口（local.ini / mx.ini，幂等）。
  ③ 启动器原子残留检查（shadow 残留会让 mx.exe 弹「已经运行」，必须走 _mx_run.py）。
  ④ 等客户端接入（/api/state connected），打印用户操作指引。
  ⑤ 观察期：客户端自发 op101 → 配对 op151（pos 一致、desc 非空）；
     客户端自发 op100 → 按 /api/state 任务态判定「已接→必有 72 / 未接→必须沉默」。
  ⑥ 注入兜底（--no-inject-fallback 关闭）：没等到客户端自发时，注入确定性检查——
     op101(pos=0) 必回 op151(pos=0, desc 含「小麦种子」)；op100(tid=999999 必未接) 必须沉默。
  ⑦ 写实验报告 MD（帧级证据 + 逐项 PASS/FAIL），退出码反映总结果。

用法：
  python _e2e_live_exp.py                    # 默认：观察 180s + 注入兜底
  python _e2e_live_exp.py --minutes 10       # 观察 10 分钟
  python _e2e_live_exp.py --no-inject-fallback   # 只信客户端自发，不注入
  python _e2e_live_exp.py --keep             # 结束后保留自己起的调试台
"""
from __future__ import print_function

import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

SIM = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SIM)
DBG = 'http://127.0.0.1:8090'
REPORT_DIR = r'C:\Users\Administrator\MX\协议资料\报告'
CLIENT_DIR = r'D:\mxsj2013\client'
PY = sys.executable or 'python'


def api(path):
    with urllib.request.urlopen(DBG + path, timeout=5) as r:
        return json.load(r)


def post(path, obj):
    req = urllib.request.Request(DBG + path, method='POST',
                                 data=json.dumps(obj).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def ensure_debugger():
    """返回 (proc, started_by_us)。已在跑则复用。"""
    try:
        api('/api/hello')
        print('[harness] 复用已在运行的调试台 %s' % DBG)
        return None, False
    except Exception:                                           # noqa: BLE001
        pass
    print('[harness] 调试台未运行 → 启动 debugger.py --no-client --no-encounter …')
    proc = subprocess.Popen([PY, os.path.join(SIM, 'debugger.py'),
                             '--no-client', '--no-encounter'], cwd=SIM)
    for _ in range(30):
        time.sleep(0.5)
        try:
            api('/api/hello')
            print('[harness] 调试台就绪')
            return proc, True
        except Exception:                                       # noqa: BLE001
            continue
    raise RuntimeError('调试台 15s 内未就绪')


def point_configs():
    import singleplayer as sp
    port = api('/api/hello')['kernel']['port']
    print('[harness] %s' % sp.rewrite_local_ini(CLIENT_DIR, port)[1])
    # ★ 必须同步重建 server7_local.bin：客户端优先读 local.ini，但「刷新列表」
    #   拉的是 HTTP 劫持件。只改前者 → 下次 refresh 又把客户端指回旧端口
    #   （2026-10-20 真机踩坑，见报告 §12.2.2；tests/_test_live_port_sync.py 守着）。
    print('[harness] %s' % sp.rebuild_server7_bin(port)[1])
    print('[harness] %s' % sp.update_mx_ini(CLIENT_DIR)[1])
    return port


def check_atoms():
    try:
        import _mx_run as R
        run, shadow = R.atom_state()
        if run or shadow:
            print('[harness] ⚠ 启动器原子残留 run=%d shadow=%d —— 必须走 _mx_run.py '
                  '（helper 会清原子），直接双击 mx.exe 会弹「更新程序已经运行」' % (run, shadow))
        else:
            print('[harness] 启动器原子干净（run=0 shadow=0），可直接双击 mx.exe')
    except Exception as e:                                      # noqa: BLE001
        print('[harness] 原子检查跳过：%r' % e)


def wait_client(timeout):
    t0 = time.time()
    announce = True
    while time.time() - t0 < timeout:
        try:
            if api('/api/state').get('connected'):
                print('[harness] ✅ 客户端已接入')
                return True
        except Exception:                                       # noqa: BLE001
            pass
        if announce:
            print('[harness] 等待客户端…请按下方指引启动并登录：')
            print('''  ┌─ 用户操作指引 ─────────────────────────────────────────┐
  │ 1) 管理员 PowerShell：cd sim && python _mx_run.py   │
  │    （弹 UAC 点「是」；它先清原子再拉 mx.exe）        │
  │ 2) 选服界面第一行「本地测试服」→ 数字帐号/6位密码    │
  │ 3) 进世界后走到村长旁点击 → 对话框：                 │
  │      选项2「我想买点东西」→ 开商店 → 悬停任意商品     │
  │      （触发 op101，本脚本自动捕获配对 op151）        │
  │      选项1「我想接个任务」→ 接任务（之后 op100 生效） │
  └──────────────────────────────────────────────────┘''')
            announce = False
        time.sleep(1)
    return False


def rows_of(f):
    return {r.get('name'): r.get('value') for r in (f.get('rows') or [])}


def main(argv):
    minutes = 10.0
    inject_fb = True
    keep = False
    it = iter(argv)
    for a in it:
        if a == '--minutes':
            minutes = float(next(it))
        elif a == '--no-inject-fallback':
            inject_fb = False
        elif a == '--keep':
            keep = True

    proc, ours = ensure_debugger()
    port = point_configs()
    check_atoms()
    hello = api('/api/hello')

    checks = []          # (名称, 方式, 结果, 证据)
    evidence = []        # 帧时间线（实验相关 op）
    WATCH = (96, 98, 99, 100, 101, 106, 147, 148, 151, 152, 72)
    since = 0

    def pull():
        """拉新帧，返回列表；同时喂给 evidence。"""
        nonlocal since
        d = api('/api/frames?since=%d' % since)
        since = d.get('last', since)
        out = []
        for f in d.get('frames', []):
            if f.get('dir') == 'RAW':
                continue
            if f.get('op') in WATCH:
                evidence.append(f)
                out.append(f)
        return out

    # ── 等客户端 ──
    connected = wait_client(minutes * 60)
    if not connected:
        print('[harness] ⏱ %d 分钟内无客户端接入 —— 转入注入兜底（只验服务端）' % minutes)

    # ── 观察期：客户端自发帧判定 ──
    print('[harness] 观察期开始（%d 分钟）…请在游戏里点村长开商店/接任务' % minutes)
    obs_deadline = time.time() + minutes * 60
    pend101 = {}         # pos -> C2S 帧
    got151 = {}          # pos -> S2C 帧
    op100_seen = []      # (帧, 任务态)
    op72_after100 = []   # tid -> S2C 帧
    last_poll = time.time()
    while time.time() < obs_deadline:
        try:
            for f in pull():
                op, dr = f['op'], f['dir']
                if op == 101 and dr == 'C2S':
                    pend101[rows_of(f).get('pos')] = f
                elif op == 151 and dr == 'S2C':
                    got151[rows_of(f).get('pos')] = f
                elif op == 100 and dr == 'C2S':
                    try:
                        tasks = api('/api/state').get('tasks', {})
                    except Exception:                           # noqa: BLE001
                        tasks = {}
                    op100_seen.append((f, tasks))
                elif op == 72 and dr == 'S2C':
                    for f100, _t in op100_seen:
                        if f100['seq'] < f['seq']:
                            op72_after100.append(f)
        except Exception as e:                                  # noqa: BLE001
            print('[harness] 轮询异常（忽略）: %r' % e)
        # 提前收尾：两项客户端判定都出了结果就退
        if pend101 and all(p in got151 for p in pend101) and op100_seen:
            time.sleep(2)                 # 再捞一会儿配对帧
            for f in pull():
                if f['op'] == 151 and f['dir'] == 'S2C':
                    got151[rows_of(f).get('pos')] = f
                elif f['op'] == 72 and f['dir'] == 'S2C':
                    op72_after100.append(f)
            break
        time.sleep(1)

    # 判定①：客户端自发 op101 → op151 配对
    ok101 = bool(pend101) and all(
        p in got151 and rows_of(got151[p]).get('desc') for p in pend101)
    if pend101:
        ev = ['C2S 101 pos=%s @%s' % (p, pend101[p]['ts']) for p in pend101]
        ev += ['S2C 151 pos=%s desc=%r @%s'
               % (p, rows_of(got151[p]).get('desc'), got151[p]['ts'])
               for p in pend101 if p in got151]
        checks.append(('op101→op151（客户端悬停查询）', '客户端自发',
                       'PASS' if ok101 else 'FAIL', '；'.join(ev)))
    else:
        checks.append(('op101→op151（客户端悬停查询）', '客户端自发', 'SKIP', '观察期内未触发'))

    # 判定②：客户端自发 op100 行为自洽
    if op100_seen:
        tid_ok = True
        ev = []
        for f100, tasks in op100_seen:
            tid = rows_of(f100).get('taskid')
            accepted = str(tid) in {str(k) for k in tasks} and \
                any(t.get('state') not in (None, 0) for k, t in tasks.items() if str(k) == str(tid))
            paired = any(rows_of(f72).get('tid') == tid for f72 in op72_after100)
            ev.append('C2S 100 tid=%s @%s（服务端任务态 accepted=%s）→ %s'
                      % (tid, f100['ts'], accepted,
                         'S2C 72 已配对' if paired else '无应答'))
            if accepted and not paired:
                tid_ok = False
            if (not accepted) and paired:
                tid_ok = False          # 未接任务却造了 TaskAdd = 服务端编造
        checks.append(('op100 行为（已接重发/未接沉默）', '客户端自发',
                       'PASS' if tid_ok else 'FAIL', '；'.join(ev)))
    else:
        checks.append(('op100 行为（已接重发/未接沉默）', '客户端自发', 'SKIP', '观察期内未触发'))

    # ── 注入兜底：确定性服务端验证 ──
    if inject_fb:
        base = api('/api/frames?since=0')['last']
        post('/api/inject', {'dir': 'C2S', 'op': 101, 'hex': '00000000'})
        time.sleep(1.5)
        inj = [f for f in api('/api/frames?since=%d' % base)['frames']
               if f.get('dir') != 'RAW']
        evidence.extend(inj)
        r151 = [f for f in inj if f['op'] == 151 and f['dir'] == 'S2C']
        ok = bool(r151) and rows_of(r151[0]).get('pos') == 0 \
            and '小麦种子' in str(rows_of(r151[0]).get('desc'))
        checks.append(('op101(pos=0)→op151「小麦种子」', '注入',
                       'PASS' if ok else 'FAIL',
                       ('S2C 151 %r' % rows_of(r151[0])) if r151 else '无 op151 应答'))

        base = api('/api/frames?since=0')['last']
        post('/api/inject', {'dir': 'C2S', 'op': 100, 'hex': '9f8601'})   # tid=999999 必未接
        time.sleep(2.5)
        inj = [f for f in api('/api/frames?since=%d' % base)['frames']
               if f.get('dir') != 'RAW']
        evidence.extend(inj)
        r72 = [f for f in inj if f['op'] == 72 and f['dir'] == 'S2C']
        checks.append(('op100(tid=999999 未接)→必须沉默', '注入',
                       'PASS' if not r72 else 'FAIL',
                       '无 TaskAdd 应答 ✓' if not r72 else '不该有却回了 %d 帧 72' % len(r72)))

    # ── 报告 ──
    npass = sum(1 for _n, _w, r, _e in checks if r == 'PASS')
    nfail = sum(1 for _n, _w, r, _e in checks if r == 'FAIL')
    ts = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(REPORT_DIR, '真机实验报告_%s.md' % ts)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# 真机实验报告（_e2e_live_exp 自动生成）%s\n\n' % ts)
        f.write('- 内核：127.0.0.1:%s（seed %s）\n' % (port, hello['kernel']['seed']))
        f.write('- 客户端接入：%s\n' % ('是' if connected else '否（仅注入兜底）'))
        f.write('- 覆盖表：%d 条（含 op151 S2CNpcDetails）\n\n' % hello['overlay'])
        f.write('## 检查清单\n\n| 检查项 | 方式 | 结果 | 证据 |\n| --- | --- | --- | --- |\n')
        for n, w, r, e in checks:
            f.write('| %s | %s | %s | %s |\n' % (n, w, r, e.replace('|', '/')))
        f.write('\n## 帧时间线（实验相关 %d 帧）\n\n' % len(evidence))
        for fr in evidence:
            rows = ' '.join('%s=%s' % (k, v) for k, v in list(rows_of(fr).items())[:5])
            f.write('- `%s` %s op=%s %s %s%s\n'
                    % (fr['ts'], fr['dir'], fr['op'], fr.get('name', ''),
                       rows[:110], '（注入）' if fr.get('injected') else ''))
    print('\n[harness] 报告已写：%s' % path)
    print('[harness] 检查 %d 项：PASS %d / FAIL %d / SKIP %d'
          % (len(checks), npass, nfail, len(checks) - npass - nfail))
    for n, w, r, e in checks:
        print('  %-4s %-34s [%s] %s' % (r, n, w, e[:90]))

    if ours and not keep:
        proc.terminate()
        print('[harness] 自己起的调试台已收（--keep 可保留）')
    return 0 if nfail == 0 and (npass > 0 or not checks) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
