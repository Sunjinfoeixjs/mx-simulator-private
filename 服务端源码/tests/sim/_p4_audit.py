# -*- coding: utf-8 -*-
u"""P4-00 基线审计：**事实采集器**（只读，不改任何状态）。

为什么要有这个脚本
------------------
P4 指令 §4.1 要求「不采信文档结论，以当前源码与实跑为准」。手写审计报告的问题
是**写完就过期**：模块被补上了、步数变了、状态改档了，文档还停在旧结论。

所以本脚本只做**采集 + 打印**，报告里的每个数字都能用
`python 服务端源码/tests/sim/_p4_audit.py` 重跑复现。谁想质疑报告，
跑一遍就知道。

采集口径
--------
  · 模块存在性：P3/P4 指令列出的候选新增文件，逐个 `os.path.isfile`
  · 特性状态：`m4_server.FEATURE_STATUS` 原表（含 `_FEATURE_SEVERITY` 排序键）
  · 存档：`SCHEMA_VERSION` + `MIGRATIONS` 链（逐级可迁移才算兼容）
  · 回归：`sim/tests/_run_all.STEPS` 的步骤名与分层（静态读，不执行）
  · 客户端证据：`客户端源码/src/data/` 下关键数据表是否存在
  · 风险扫描：源码里 `TODO` / `FIXME` / `XXX` / `未实现` 的分布

用法：
    python 服务端源码/tests/sim/_p4_audit.py            # 人读
    python 服务端源码/tests/sim/_p4_audit.py --md       # 直接输出 markdown 表格
"""
from __future__ import print_function

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CLIENT_DATA = os.path.join(MX, '客户端源码', 'src', 'data')
TESTS = os.path.join(os.path.dirname(HERE), '')
RUNNER = os.path.join(HERE, 'tests', '_run_all.py')

# P3/P4 指令里点名的候选新增模块 → 归属工作包
EXPECTED_MODULES = [
    # P3-03 高级战斗
    ('combat_effects.py', 'P3-03'),
    ('enemy_ai.py', 'P3-03'),
    # ★ 原计划叫 `battle_rules.py`，落地时改名 `battle_rng.py`：
    #   实际职责只是「随机源 + 流分离」，不含战斗规则判定 —— 叫 rules 会误导。
    ('battle_rng.py', 'P3-03'),
    ('buff_data.py', 'P3-03'),
    ('gen_buff_data.py', 'P3-03'),
    # P3-04 动态世界
    ('spawn_rules.py', 'P3-04'),
    ('world_events.py', 'P3-04'),
    # P3-05 经济
    ('economy_service.py', 'P3-05'),
    ('economy_ledger.py', 'P3-05'),
    # P3-06 单人挑战
    ('challenge_data.py', 'P3-06'),
    ('challenge_engine.py', 'P3-06'),
    # P3-01/02 已落地
    ('progression_service.py', 'P3-01'),
    ('equipment_rules.py', 'P3-01'),
    ('equip_data.py', 'P3-01'),
    ('pet_progression.py', 'P3-02'),
    ('pet_skill_service.py', 'P3-02'),
    ('pet_skill_data.py', 'P3-02'),
    # P4 候选
    ('rule_evidence.py', 'P4-01'),
    ('state_sync_audit.py', 'P4-02'),
    ('balance_simulator.py', 'P4-05'),
    ('save_integrity.py', 'P4-06'),
    ('content_validator.py', 'P4-04'),
    ('release_check.py', 'P4-08'),
]

# 客户端真值表（P4-01 校准要用的原始证据）
CLIENT_TABLES = [
    'itemdata.py', 'petskill.py', 'petdata.py', 'monsterdata.py',
    'npcposdata.py', 'magicdata.py', 'skilldata.py', 'craftdata.py',
    'petfightdata.py', 'equipdata.py',
]

RISK_PAT = re.compile(u'(TODO|FIXME|XXX|HACK|未实现|待实现|不可用)')


def rel(p):
    try:
        return os.path.relpath(p, MX).replace('\\', '/')
    except ValueError:
        return p


def scan_modules():
    rows = []
    for name, pkg in EXPECTED_MODULES:
        p = os.path.join(HERE, name)
        ok = os.path.isfile(p)
        rows.append({'file': name, 'pkg': pkg, 'exists': ok,
                     'lines': _count_lines(p) if ok else 0})
    return rows


def _count_lines(path):
    try:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def scan_client_tables():
    out = []
    for name in CLIENT_TABLES:
        p = os.path.join(CLIENT_DATA, name)
        out.append({'file': name, 'exists': os.path.isfile(p),
                    'size': os.path.getsize(p) if os.path.isfile(p) else 0})
    return out


def scan_feature_status(m):
    sev = getattr(m, '_FEATURE_SEVERITY', {})
    rows = []
    for k, v in m.FEATURE_STATUS.items():
        rows.append({'key': k, 'title': m.feature_label(k), 'status': v,
                     'rank': sev.get(v, 99)})
    rows.sort(key=lambda r: (r['rank'], r['key']))
    return rows


def scan_schema(m):
    mig = getattr(m, 'MIGRATIONS', {}) or {}
    return {'version': getattr(m, 'SCHEMA_VERSION', None),
            'migrations': sorted(mig.keys()),
            'chain_ok': _chain_ok(mig, getattr(m, 'SCHEMA_VERSION', 0))}


def _chain_ok(mig, ver):
    """迁移链是否从 0 一级一级能走到 SCHEMA_VERSION（缺一级 = 旧档读不了）。"""
    for i in range(int(ver or 0)):
        if i not in mig:
            return False
    return True


def scan_runner():
    """静态读 `_run_all.STEPS`（不执行），拿到步骤名与分层。"""
    src = io.open(RUNNER, encoding='utf-8').read()
    i = src.find('STEPS = [')
    if i < 0:
        return []
    j = src.find('\n]', i)
    block = src[i:j if j > 0 else len(src)]
    rows = []
    for ln in block.splitlines():
        mm = re.match(r"\s*\(\s*'([^']+)'\s*,\s*'([^']+)'", ln)
        if mm:
            rows.append({'name': mm.group(1), 'layer': mm.group(2)})
    return rows


def scan_risks():
    """源码里 TODO/FIXME/未实现 … 的分布（按文件计数，只报有命中的）。"""
    out = {}
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith('.py') or fn.startswith('_p4_audit'):
            continue
        p = os.path.join(HERE, fn)
        n = 0
        try:
            with io.open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if RISK_PAT.search(line):
                        n += 1
        except OSError:
            continue
        if n:
            out[fn] = n
    return out


def code_size():
    tot = {}
    for d in (HERE, os.path.join(os.path.dirname(HERE))):
        for fn in os.listdir(d):
            if fn.endswith('.py'):
                tot[fn] = _count_lines(os.path.join(d, fn))
    core = ['m4_server.py', 'frames.py', 'tap.py', 'world_config.py',
            'battle_formulas.py', 'damage_model.py', 'quest_data.py',
            'bus.py', 'debugger.py', 'singleplayer.py']
    return {k: tot.get(k, 0) for k in core}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--md', action='store_true', help='输出 markdown 表格')
    args = ap.parse_args(argv)

    sys.path.insert(0, HERE)
    import m4_server as m

    mods = scan_modules()
    tabs = scan_client_tables()
    feats = scan_feature_status(m)
    sch = scan_schema(m)
    steps = scan_runner()
    risks = scan_risks()
    size = code_size()

    if args.md:
        print(u'### A. 模块存在性（P3/P4 指令点名）\n')
        print(u'| 模块 | 归属 | 存在 | 行数 |')
        print(u'|---|---|---|---:|')
        for r in mods:
            print(u'| `%s` | %s | %s | %s |'
                  % (r['file'], r['pkg'], u'✓' if r['exists'] else u'**✗**',
                     r['lines'] or u'—'))
        print(u'\n### B. 客户端真值表\n')
        print(u'| 表 | 存在 | 字节 |')
        print(u'|---|---|---:|')
        for r in tabs:
            print(u'| `%s` | %s | %s |'
                  % (r['file'], u'✓' if r['exists'] else u'**✗**', r['size']))
        print(u'\n### C. FEATURE_STATUS（%d 项，按严重度）\n' % len(feats))
        print(u'| 键 | 标题 | 状态 |')
        print(u'|---|---|---|')
        for r in feats:
            print(u'| `%s` | %s | `%s` |' % (r['key'], r['title'], r['status']))
        print(u'\n### D. 存档 schema\n')
        print(u'- SCHEMA_VERSION = **%s**' % sch['version'])
        print(u'- MIGRATIONS 键 = %s' % sch['migrations'])
        print(u'- 迁移链完整（0..N 每一级都在）= **%s**' % sch['chain_ok'])
        print(u'\n### E. 回归步骤（静态读 STEPS，%d 步）\n' % len(steps))
        by_layer = {}
        for s in steps:
            by_layer.setdefault(s['layer'], []).append(s['name'])
        for layer in sorted(by_layer):
            print(u'- **%s**（%d）：%s'
                  % (layer, len(by_layer[layer]),
                     u'、'.join(by_layer[layer])))
        print(u'\n### F. 核心模块规模\n')
        print(u'| 模块 | 行数 |')
        print(u'|---|---:|')
        for k, v in size.items():
            print(u'| `%s` | %d |' % (k, v))
        print(u'\n### G. 风险标记分布（TODO/FIXME/未实现…，共 %d 处）\n'
              % sum(risks.values()))
        print(u'| 文件 | 处数 |')
        print(u'|---|---:|')
        for k, v in sorted(risks.items(), key=lambda x: -x[1])[:15]:
            print(u'| `%s` | %d |' % (k, v))
        return 0

    print(u'== 模块 ==')
    for r in mods:
        print(u'  %s %-24s %-7s %s'
              % (u'OK  ' if r['exists'] else u'MISS', r['file'], r['pkg'],
                 (u'%d 行' % r['lines']) if r['exists'] else u''))
    print(u'== 客户端表 ==')
    for r in tabs:
        print(u'  %s %s' % (u'OK  ' if r['exists'] else u'MISS', r['file']))
    print(u'== FEATURE_STATUS %d 项 ==' % len(feats))
    for r in feats:
        print(u'  %-22s %-22s %s' % (r['key'], r['status'], r['title']))
    print(u'== schema == %s  migrations=%s  chain_ok=%s'
          % (sch['version'], sch['migrations'], sch['chain_ok']))
    print(u'== 回归 %d 步 ==' % len(steps))
    print(u'== 风险标记 %d 处 ==' % sum(risks.values()))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
