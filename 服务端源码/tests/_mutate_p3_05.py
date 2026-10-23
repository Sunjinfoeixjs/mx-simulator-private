# -*- coding: utf-8 -*-
"""P3-05 变异验证：把修复逐个改坏，确认测试会判红。

纪律：每个变异独立施加 → 跑测试 → 还原 → 校验 sha256 与原件一致。
「打不红」要么是覆盖漏洞，要么是变异构造错了 —— 两种都要如实记录。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
TEST = os.path.join(HERE, '_test_p3_05_economy.py')
PY = sys.executable


def sha(p):
    return hashlib.sha256(io.open(p, 'rb').read()).hexdigest()[:16]


MUTATIONS = [
    ('M1 卖出改回裸写', os.path.join(SIM, 'm4_server.py'),
     "        self._econ_gain(conn, gain, 'NPC 卖出 %s ×%d' % (item_name_of(shape), gone))",
     "        self.coins += gain\n"
     "        conn.sendall(build_prop_change(PROP_IDX_GOLD, self.coins, 4))"),
    ('M2 去掉交任务幂等键', os.path.join(SIM, 'm4_server.py'),
     "        self._econ_gain(conn, TASK_REWARD_COINS, '新手任务奖励',\n"
     "                        idem='turnin:%d' % TASK_NEWBIE_ID)",
     "        self._econ_gain(conn, TASK_REWARD_COINS, '新手任务奖励')"),
    ('M3 GM 不发 PropChange', os.path.join(SIM, 'm4_server.py'),
     "            if res.ok and res.delta:\n"
     "                self._gm_send(build_prop_change(PROP_IDX_GOLD, self.coins, 4))",
     "            if False:\n"
     "                self._gm_send(build_prop_change(PROP_IDX_GOLD, self.coins, 4))"),
    ('M4 金钱 PropChange 宽度写错(4→2)', os.path.join(SIM, 'm4_server.py'),
     "    eled.KIND_COIN: (PROP_IDX_GOLD, 4),",
     "    eled.KIND_COIN: (PROP_IDX_GOLD, 2),"),
    ('M5 账本不再校验 before+delta==after', os.path.join(SIM, 'economy_ledger.py'),
     "        if before + delta != after:\n            return None",
     "        if False:\n            return None"),
    ('M6 charge_multi 改回「记一笔改一个」（破坏原子）', os.path.join(SIM, 'economy_service.py'),
     "            results.append(_blank(True, R_OK, reason, kind=kind, delta=-amt,\n"
     "                                  before=b, after=a, idem=idem))\n"
     "\n"
     "        # ③ 改值 + 通知",
     "            results.append(_blank(True, R_OK, reason, kind=kind, delta=-amt,\n"
     "                                  before=b, after=a, idem=idem))\n"
     "            self._set(kind, b - amt)\n"
     "\n"
     "        # ③ 改值 + 通知"),
]

rows = []
try:
    for name, path, old, new in MUTATIONS:
        raw = io.open(path, encoding='utf-8').read()
        if old not in raw:
            rows.append((name, '构造错', '待替换片段未找到'))
            print('!! %s: 待替换片段未找到（变异构造错，不是覆盖漏洞）' % name)
            continue
        bak = path + '.p305bak'
        shutil.copy2(path, bak)
        h0 = sha(path)
        try:
            io.open(path, 'w', encoding='utf-8', newline='').write(raw.replace(old, new, 1))
            p = subprocess.run([PY, TEST], cwd=HERE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            out = p.stdout.decode('utf-8', 'replace')
            fails = [ln[5:].strip() for ln in out.splitlines() if ln.startswith('FAIL')]
            verdict = '判红' if p.returncode != 0 else '打不红'
            rows.append((name, verdict, '; '.join(fails[:3]) or '(无 FAIL 行)'))
            print('%s  %-34s  %s' % (verdict, name, ('; '.join(fails[:3]) or '')[:150]))
        finally:
            shutil.copy2(bak, path)
            os.remove(bak)
            assert sha(path) == h0, '还原失败：%s' % path
finally:
    print('')
    print('=' * 70)
    for name, verdict, detail in rows:
        print('%-8s %-38s %s' % (verdict, name, detail[:110]))
    # 还原后再跑一次，确认是绿的（否则前面的「判红」没有意义）
    p = subprocess.run([PY, TEST], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print('还原后复跑：%s' % ('绿' if p.returncode == 0 else '仍然红！'))
