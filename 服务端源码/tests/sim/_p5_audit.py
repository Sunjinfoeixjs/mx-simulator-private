# -*- coding: utf-8 -*-
"""P5-00 基线采集器：把「P1–P5 各阶段实际落地了什么」变成可复核的数据。

原则：**只读 + 实测**，不读任何"计划"或"报告里的声称"。
  · 模块是否存在 → os.path.isfile
  · 行数         → 实际读文件数
  · FEATURE_STATUS → import m4_server 后读真实字典
  · 存档字段     → 读 _save_snapshot / dumps 的真实键
  · 测试         → 静态读 STEPS + 数 _test_*.py
  · 文档         → 列 docs/ 与 协议资料/报告/P3/

用法：
    python _p5_audit.py            # 人读
    python _p5_audit.py --md       # markdown 表格（贴进 P5_BASELINE_AUDIT.md）
    python _p5_audit.py --json     # 机器读
"""
from __future__ import print_function, unicode_literals

import ast
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 别数错层：HERE = MX/服务端源码/tests/sim，所以要往上 **三层** 才是 MX 根。
#   先前写成两层（只到 服务端源码），导致 docs/ 与 报告/ 全被误报为「缺失」。
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DOCS = os.path.join(MX, 'docs')
REPORTS = os.path.join(MX, '协议资料', '报告')

if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ── 各阶段「点名要交的模块」→ 实际是否存在 ──────────────────────────
# 左列是 P3/P4/P5 指令里**逐字写过的模块名**，不是我事后编的。
EXPECTED = [
    # (阶段, 模块名, 说明)
    ('P3-01', 'progression_service.py', '角色最终属性唯一入口'),
    ('P3-01', 'equipment_rules.py', '装备槽位/等级门判定'),
    ('P3-01', 'equip_data.py', '装备真值表（生成物）'),
    ('P3-02', 'pet_progression.py', '宠物三层属性唯一入口'),
    ('P3-02', 'pet_skill_service.py', '宠物技能表增删'),
    ('P3-02', 'pet_skill_data.py', '宠物技能真值表（生成物）'),
    ('P3-03', 'battle_rng.py', '战斗随机源（原名 battle_rules.py）'),
    ('P3-03', 'combat_effects.py', '状态效果机制'),
    ('P3-03', 'enemy_ai.py', '敌方行动决策'),
    ('P3-03', 'buff_data.py', '状态真值表（生成物）'),
    ('P3-04', 'spawn_rules.py', '动态刷新规则'),
    ('P3-04', 'world_events.py', '世界事件'),
    ('P3-05', 'economy_service.py', '经济服务'),
    ('P3-05', 'economy_ledger.py', '经济流水'),
    ('P3-06', 'challenge_data.py', '挑战数据'),
    ('P3-06', 'challenge_engine.py', '挑战引擎'),
    ('P4-01', 'rule_evidence.py', '规则证据库'),
    ('P4-02', 'state_sync_audit.py', '状态一致性审计'),
    ('P4-03', 'balance_simulator.py', '数值模拟/校准'),
    ('P4-04', 'content_validator.py', '内容校验'),
    ('P4-06', 'save_integrity.py', '存档完整性'),
    ('P4-08', 'release_check.py', '发布校验'),
    ('P5-03', 'save_integrity.py', '存档完整性（P4-06 同名，共用）'),
    ('P5-02', 'protocol_golden.py', '协议黄金样本'),
    ('P5-07', 'diagnostic_export.py', '诊断包导出'),
    ('P5-08', 'version_info.py', '版本元数据'),
    ('P5-08', 'release_check.py', '发布校验（P4-08 同名，共用）'),
]

# 各阶段「点名要交的文档」
EXPECTED_DOCS = [
    ('P4', 'P4_BASELINE_AUDIT.md'),
    ('P4', 'P4_RULE_EVIDENCE.md'),
    ('P4', 'P4_STATE_CONSISTENCY.md'),
    ('P4', 'P4_BALANCE_CALIBRATION.md'),
    ('P4', 'P4_CONTENT_VALIDATION.md'),
    ('P4', 'P4_ECONOMY_SAFETY.md'),
    ('P4', 'P4_SAVE_INTEGRITY.md'),
    ('P4', 'P4_DEBUGGING.md'),
    ('P4', 'P4_RELEASE_QUALITY.md'),
    ('P4', 'P4_ACCEPTANCE.md'),
    ('P5', 'P5_BASELINE_AUDIT.md'),
    ('P5', 'P5_GAMEPLAY_ACCEPTANCE.md'),
    ('P5', 'P5_PROTOCOL_COMPATIBILITY.md'),
    ('P5', 'P5_SAVE_COMPATIBILITY.md'),
    ('P5', 'P5_AUTOMATION.md'),
    ('P5', 'P5_STABILITY_REPORT.md'),
    ('P5', 'P5_GAMEPLAY_QUALITY.md'),
    ('P5', 'P5_DIAGNOSTICS.md'),
    ('P5', 'P5_RELEASE_MANAGEMENT.md'),
    ('P5', 'P5_FINAL_ACCEPTANCE.md'),
]

# P3 八个工作包（P3 指令原文编号）
P3_PACKAGES = ['P3-01', 'P3-02', 'P3-03', 'P3-04', 'P3-05', 'P3-06', 'P3-07', 'P3-08']
# P4 九个工作包（P4 指令原文编号；P4-00 是审计）
P4_PACKAGES = ['P4-00', 'P4-01', 'P4-02', 'P4-03', 'P4-04',
               'P4-05', 'P4-06', 'P4-07', 'P4-08']


def count_lines(path):
    try:
        return sum(1 for _ in io.open(path, 'r', encoding='utf-8', errors='replace'))
    except (IOError, OSError):
        return 0


def scan_modules():
    out = []
    for stage, name, desc in EXPECTED:
        p = os.path.join(HERE, name)
        out.append({
            'stage': stage,
            'module': name,
            'desc': desc,
            'exists': os.path.isfile(p),
            'lines': count_lines(p) if os.path.isfile(p) else 0,
        })
    return out


def scan_docs():
    out = []
    for stage, name in EXPECTED_DOCS:
        p = os.path.join(DOCS, name)
        out.append({'stage': stage, 'doc': name,
                    'exists': os.path.isfile(p),
                    'bytes': (os.path.getsize(p) if os.path.isfile(p) else 0)})
    return out


def scan_reports():
    """P3 报告（协议资料/报告/P3/）实际有哪些。"""
    d = os.path.join(REPORTS, 'P3')
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith('.md'))


def scan_feature():
    try:
        import m4_server as m
    except Exception as e:                            # noqa: BLE001
        return {'error': '%s: %s' % (type(e).__name__, e)}
    import collections
    cnt = collections.Counter(m.FEATURE_STATUS.values())
    return {
        'total': len(m.FEATURE_STATUS),
        'by_status': dict(cnt),
        'items': dict(m.FEATURE_STATUS),
        'missing_title': sorted(k for k in m.FEATURE_STATUS
                                if k not in m.FEATURE_TITLE),
        'legend': list(getattr(m, 'FEATURE_STATUS_LEGEND', {}) or {}),
    }


def _ast_str(node):
    """从 ast 节点取字符串常量，兼容 3.8 前后的 Constant / Str。"""
    if isinstance(node, getattr(ast, 'Constant', ())):
        v = node.value
        return v if isinstance(v, str) else None
    if isinstance(node, getattr(ast, 'Str', ())):     # py<3.8
        return node.s
    return None


def _find_func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _dict_literal_keys(func, var_name):
    """取 `var_name = { ... }` 里的字符串键。"""
    out = []
    if func is None:
        return out
    for n in ast.walk(func):
        if not isinstance(n, ast.Assign) or not isinstance(n.value, ast.Dict):
            continue
        if not any(isinstance(t, ast.Name) and t.id == var_name for t in n.targets):
            continue
        for k in n.value.keys:
            s = _ast_str(k)
            if s:
                out.append(s)
    return out


def _subscript_keys(func, var_name):
    """取 `var_name['xxx'] = ...` 追加的键（_persist 末尾补 _save_tag/_schema）。"""
    out = []
    if func is None:
        return out
    for n in ast.walk(func):
        if not isinstance(n, ast.Assign) or not n.targets:
            continue
        t = n.targets[0]
        if not isinstance(t, ast.Subscript):
            continue
        if not (isinstance(t.value, ast.Name) and t.value.id == var_name):
            continue
        s = _ast_str(t.slice)
        if s:
            out.append(s)
    return out


def scan_schema():
    """存档字段：用 ast 解析 `_persist()` 里**真实构造**的那份 dict。

    ★ 为什么不用正则扫 `d['x'] = `（2026-10-23 修）：那会扫到几百处零散赋值
      （局部变量、临时 dict、事件字段），既不是「存档字段」也不是任何有意义
      的集合——初版只采到 7 个键，与真实存档的 30+ 键完全不符。

    `_persist()` 里 `data = {...}` 是落盘的**唯一真相**；
    `data['_save_tag'] / data['_schema']` 在其后以下标形式补入，一并收进来。
    迁移链的默认值单独取，用来核对「迁移补的字段」是否都在落盘集合里。
    """
    try:
        import m4_server as m
    except Exception as e:                            # noqa: BLE001
        return {'error': str(e)}

    src = io.open(os.path.join(HERE, 'm4_server.py'), 'rb').read().decode(
        'utf-8', 'replace')
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {'error': 'SyntaxError: %s' % e}

    persist = _find_func(tree, '_persist')
    keys = _dict_literal_keys(persist, 'data')
    tail = _subscript_keys(persist, 'data')
    mig = _find_func(tree, '_migrate_1_to_2')
    mig_keys = _dict_literal_keys(mig, 'defaults')

    # 保留字面顺序（读起来与源码 _persist 一一对应），同时去重
    fields = []
    for k in keys + tail:
        if k not in fields:
            fields.append(k)
    return {
        'version': getattr(m, 'SCHEMA_VERSION', None),
        'migrations': sorted(getattr(m, 'MIGRATIONS', {}) or {}),
        'persist_fn_found': persist is not None,
        'fields': fields,
        'field_count': len(fields),
        'main_keys': len(set(keys)),
        'tail_keys': sorted(set(tail)),
        'migration_defaults': sorted(set(mig_keys)),
        'migration_only': sorted(set(mig_keys) - set(fields)),
    }


def scan_dispatch():
    try:
        import m4_server as m
    except Exception as e:                            # noqa: BLE001
        return {'error': str(e)}
    import frames as bf
    return {
        'dispatch_pre_war_ops': len(getattr(m, 'DISPATCH_PRE_WAR_OPS', []) or []),
        'frames_spec': len(getattr(bf, 'SPEC', {}) or {}),
        'build_star': len([n for n in dir(m) if n.startswith('build_')]),
    }


def scan_tests():
    tests_dir = os.path.join(os.path.dirname(HERE), '')   # 服务端源码/tests/
    tests_dir = os.path.dirname(HERE)
    files = []
    if os.path.isdir(tests_dir):
        files = sorted(f for f in os.listdir(tests_dir)
                       if f.startswith('_test_') and f.endswith('.py'))
    runner = os.path.join(tests_dir, 'sim', 'tests', '_run_all.py')
    steps = 0
    if os.path.isfile(runner):
        src = io.open(runner, 'rb').read().decode('utf-8', 'replace')
        steps = len(re.findall(r"^\s*\('.*?',\s*'(?:proto|dispatch|smoke|doc)'",
                               src, re.M))
    return {'test_files': len(files), 'files': files, 'steps': steps}


def scan_infra():
    """基础设施能力（P5-03/07/08 要用）：有还是没有，不猜。"""
    checks = {
        '启动器 singleplayer.py': os.path.isfile(os.path.join(HERE, 'singleplayer.py')),
        '抓包 tap.py': os.path.isfile(os.path.join(HERE, 'tap.py')),
        '调试台 debugger.py': os.path.isfile(os.path.join(HERE, 'debugger.py')),
        'GM 面板 gm_dashboard.html': os.path.isfile(os.path.join(HERE, 'gm_dashboard.html')),
        '世界配置 world_config.py': os.path.isfile(os.path.join(HERE, 'world_config.py')),
        '事件总线 bus.py': os.path.isfile(os.path.join(HERE, 'bus.py')),
    }
    src = io.open(os.path.join(HERE, 'm4_server.py'), 'rb').read().decode(
        'utf-8', 'replace')
    checks['存档备份函数'] = bool(re.search(r'def\s+\w*backup', src))
    checks['存档恢复函数'] = bool(re.search(r'def\s+\w*restore', src))
    checks['存档完整性校验'] = bool(re.search(r'def\s+\w*(integrity|verify_save)', src))
    checks['存档迁移链'] = bool(re.search(r'^MIGRATIONS\s*=', src, re.M))
    checks['版本元数据'] = bool(re.search(r'^BUILD_INFO\s*=', src, re.M)) or \
        os.path.isfile(os.path.join(HERE, 'version_info.py'))
    checks['诊断包导出'] = bool(re.search(r'def\s+\w*diagnos', src))
    return checks


def scan_client_evidence():
    """真实客户端证据：目录在不在、验收记录的实际状态。"""
    exe = os.path.join('D:', os.sep, 'mxsj2013', 'client', 'mx.exe')
    acc = os.path.join(REPORTS, '验收', '人工GUI验收_真实客户端.md')
    state = 'missing'
    if os.path.isfile(acc):
        t = io.open(acc, 'r', encoding='utf-8', errors='replace').read()
        if '未执行' in t[:3000]:
            state = 'template_unexecuted'
        elif 'PASS' in t:
            state = 'has_pass_marks'
        else:
            state = 'unknown'
    shots = []
    sd = os.path.join(REPORTS, '截图')
    if os.path.isdir(sd):
        shots = sorted(f for f in os.listdir(sd) if f.endswith('.png'))
    return {
        'client_exe': os.path.isfile(exe),
        'client_path': exe if os.path.isfile(exe) else None,
        'gui_acceptance_state': state,
        'gui_acceptance_file': acc if os.path.isfile(acc) else None,
        'screenshots': len(shots),
        'screenshot_names': shots,
    }


def scan_git():
    gitdir = os.path.join(MX, '.git')
    return {'is_repo': os.path.isdir(gitdir),
            'note': '' if os.path.isdir(gitdir) else '无版本控制：无 diff / 无回滚'}


def collect():
    return {
        'modules': scan_modules(),
        'docs': scan_docs(),
        'p3_reports': scan_reports(),
        'feature': scan_feature(),
        'schema': scan_schema(),
        'dispatch': scan_dispatch(),
        'tests': scan_tests(),
        'infra': scan_infra(),
        'client': scan_client_evidence(),
        'git': scan_git(),
    }


def stage_rollup(mods):
    """每个阶段「点名模块」的到位率。"""
    out = {}
    for m in mods:
        s = m['stage']
        d = out.setdefault(s, {'total': 0, 'have': 0, 'lines': 0, 'miss': []})
        d['total'] += 1
        if m['exists']:
            d['have'] += 1
            d['lines'] += m['lines']
        else:
            d['miss'].append(m['module'])
    return out


def render_md(data):
    L = []
    A = L.append
    A('### 模块到位情况\n')
    A('| 阶段 | 到位 / 应到 | 行数 | 缺失 |')
    A('|---|---:|---:|---|')
    roll = stage_rollup(data['modules'])
    for s in sorted(roll):
        d = roll[s]
        A('| %s | %d / %d | %d | %s |'
          % (s, d['have'], d['total'], d['lines'],
             '、'.join('`%s`' % x for x in d['miss']) or '—'))
    A('')
    A('### 文档到位情况\n')
    A('| 阶段 | 到位 / 应到 | 缺失 |')
    A('|---|---:|---|')
    for stage in ('P4', 'P5'):
        have = [d['doc'] for d in data['docs']
                if d['stage'] == stage and d['exists']]
        miss = [d['doc'] for d in data['docs']
                if d['stage'] == stage and not d['exists']]
        A('| %s | %d / %d | %s |'
          % (stage, len(have), len(have) + len(miss),
             '、'.join('`%s`' % x for x in miss) or '—'))
    A('')
    A('### 基础设施能力\n')
    A('| 能力 | 有 |')
    A('|---|---|')
    for k, v in data['infra'].items():
        A('| %s | %s |' % (k, '✅' if v else '❌'))
    A('')
    A('### 真实客户端证据\n')
    c = data['client']
    A('| 项 | 值 |')
    A('|---|---|')
    A('| 客户端可执行文件 | %s |' % ('✅ `%s`' % c['client_path']
                                     if c['client_exe'] else '❌ 不可达'))
    A('| GUI 验收记录状态 | `%s` |' % c['gui_acceptance_state'])
    A('| 真机截图 | %d 张 |' % c['screenshots'])
    A('')
    A('### 存档结构\n')
    s = data['schema']
    if 'error' in s:
        A('采集失败：`%s`' % s['error'])
    else:
        A('| 项 | 值 |')
        A('|---|---|')
        A('| `SCHEMA_VERSION` | %s |' % s.get('version'))
        A('| 迁移链 | %s |' % (s.get('migrations') or '—'))
        A('| `_persist()` 定位 | %s |' % ('✅' if s.get('persist_fn_found') else '❌'))
        A('| 落盘字段数 | %d（主 dict %d + 追加 %d） |'
          % (s.get('field_count', 0), s.get('main_keys', 0),
             len(s.get('tail_keys') or [])))
        A('| 迁移补的字段 | %d 个：%s |'
          % (len(s.get('migration_defaults') or []),
             '、'.join('`%s`' % x for x in (s.get('migration_defaults') or [])) or '—'))
        A('| 迁移有、落盘无 | %s |'
          % ('、'.join('`%s`' % x for x in (s.get('migration_only') or [])) or '—'))
        A('')
        A('落盘键全表（按字面顺序）：\n')
        A('`%s`' % '`、`'.join(s.get('fields') or []))
    A('')
    return '\n'.join(L)


def main(argv):
    data = collect()
    if '--json' in argv:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if '--md' in argv:
        print(render_md(data))
        return 0

    print('== 模块 ==')
    for m in data['modules']:
        print('  %-5s %-26s %s %s'
              % ('OK' if m['exists'] else 'MISS', m['module'], m['stage'],
                 ('%d 行' % m['lines']) if m['exists'] else ''))
    print('\n== 阶段小结 ==')
    for s, d in sorted(stage_rollup(data['modules']).items()):
        print('  %-6s %d/%d 到位，%d 行' % (s, d['have'], d['total'], d['lines']))
    print('\n== 文档 ==')
    for d in data['docs']:
        if d['exists']:
            print('  OK   %s (%d 字节)' % (d['doc'], d['bytes']))
    miss = [d['doc'] for d in data['docs'] if not d['exists']]
    print('  缺失 %d 份：%s' % (len(miss), '、'.join(miss)))
    print('\n== FEATURE_STATUS ==')
    f = data['feature']
    if 'error' in f:
        print('  ERROR', f['error'])
    else:
        print('  共 %d 项：%s' % (f['total'], f['by_status']))
        print('  缺中文标题：%s' % (f['missing_title'] or '无'))
    print('\n== 存档 ==')
    s = data['schema']
    if 'error' in s:
        print('  ERROR', s['error'])
    else:
        print('  schema=%s 迁移链=%s 字段 %d 个'
              % (s.get('version'), s.get('migrations'), s.get('field_count', 0)))
        print('  键：%s' % ' '.join(s.get('fields') or []))
        print('  迁移默认里落盘没有的：%s' % (s.get('migration_only') or '无'))
    print('\n== 分派/协议 ==')
    print('  %s' % data['dispatch'])
    print('\n== 测试 ==')
    t = data['tests']
    print('  _test_*.py %d 个，回归 %d 步' % (t['test_files'], t['steps']))
    print('\n== 基础设施 ==')
    for k, v in data['infra'].items():
        print('  %s %s' % ('✅' if v else '❌', k))
    print('\n== 客户端证据 ==')
    for k, v in data['client'].items():
        print('  %s: %s' % (k, v))
    print('\n== 版本控制 ==')
    print('  %s' % data['git'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
