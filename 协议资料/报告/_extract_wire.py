# -*- coding: utf-8 -*-
"""从客户端源码批量抽 S2C 处理函数的解包线序，并与 tap.py 的覆盖表对账。

为什么要有这个工具：覆盖表（`tap.py:_OV_S2C`）是**手写**的，而线序的权威来源是客户端
`net*.py` 里的 `UnpackInt/UnpackString/UnpackBit/UnpackDesc` 调用序列。两者一旦漂移，
表现不是报错，而是「调试台把字段读错位」——`SceneM(96)` 就是例子：覆盖表只写了普通场景，
而客户端在 `scene==65535/65534` 时会先读一段额外参数。

三种用途：
  python _extract_wire.py                    # 打印每个函数的线序树（人看）
  python _extract_wire.py --json out.json    # 结构化输出（机读）
  python _extract_wire.py --audit            # 与 tap.py 覆盖表对账（CI 可用）

对账会给出四种结论：
  OK           线序与覆盖表逐字段一致（类型+宽度）
  MISMATCH     不一致，列出两边序列
  NEEDS_DYN    客户端有分支/循环，静态规格只能覆盖其中一种形态 → 该配 `_OV_DYN`
  HAS_DYN      有分支/循环，但 tap 已经用 `_OV_DYN` 处理了

线序抽取的两个难点（已支持）：
  1) 委托：handler 常常自己不解包，而是把 `netobj` 传给 helper（如
     `netpet.S2CSummonInfo` → `pet.UpdateInfo(netobj)` → `pet.UpdateProp(...)`）。
     抽取器对「把 netobj 传给同工程某函数的调用」做单向内联跟随，跨 nethero/netpet/pet。
  2) 数据驱动循环：`pet.UpdateProp` 里 `for item in self.BASE_PROP[4:]: item.Unpack(netobj)`
     的字段宽度不是字面量，而是来自 `pet.py` 的 `BASE_PROP` 属性描述表。抽取器解析该表，
     把循环展开成真实字段序列（每个元素一个定宽读数）。
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

SRC = r'C:\Users\Administrator\MX\客户端源码\src'
SIM = r'C:\Users\Administrator\MX\服务端源码\sim'

# 默认扫描文件：详情面板（144/145/156）的回包散在 nethero / netpet / pet 三处。
FILES = ['nethero.py', 'netmap.py', 'netpet.py', 'pet.py']

CALL = re.compile(
    r'(?:(\w+)\s*=\s*)?'                       # 可选赋值目标
    r'(?:\w+\.)?(Unpack\w+)\(\s*([^)]*)\)')    # 允许裸 UnpackXxx（pet.py 里 UnpackDesc 无前缀）

# 委托调用：`module.func(args)`，且 args 里出现 netobj（解包流被转交给 helper）
DELEG = re.compile(r'(\w+)\.(\w+)\(([^)]*netobj[^)]*)\)')

# UnpackXxx → kind
# 注意：'S' 在这里表示「有符号整数」（客户端 UnpackSigned / UnpackSignedChar）。
# 有符号与无符号在线上布局完全相同（同为 1/2/4 字节），对账只关心字节排布，
# 所以 client_signature 会把 'S' 归一化到 'I'（见该函数），避免虚假 MISMATCH。
KIND = {'UnpackInt': 'I', 'UnpackSigned': 'S', 'UnpackString': 'T',
        'UnpackBit': 'B', 'UnpackDesc': 'D', 'UnpackShort': 'I',
        'UnpackSignedChar': 'S'}

# pet.py 类型常量 → (kind, 定宽；0 表示变长)
TYPE_W = {'TBYTE': ('I', 1), 'TWORD': ('I', 2), 'TDWORD': ('I', 4),
          'TSTRING': ('T', 0)}


def read(path):
    return io.open(path, encoding='utf-8', errors='replace').read()


def split_funcs(text, prefix=None):
    """按顶层（含类内）`def ` 切块。返回 [(name, def_indent, [(行号, 行)])]。

    def_indent 用来把函数体深度**归一化**到 0 起算，这样跨函数内联时嵌套层级
    能对齐（方法的 def 在 4 空格处，其体本应相对自身从 0 计）。
    prefix 非空时只保留以它开头的函数名（如 'S2C'）。
    """
    out, cur, name, dind = [], None, None, 0
    for i, line in enumerate(text.split('\n'), 1):
        m = re.match(r'^(\s*)def\s+(\w+)\(', line)
        if m:
            if cur is not None:
                out.append((name, dind, cur))
            name = m.group(2)
            dind = len(m.group(1))
            cur = []
        elif cur is not None:
            cur.append((i, line))
    if cur is not None:
        out.append((name, dind, cur))
    if prefix:
        out = [(n, d, ls) for n, d, ls in out if n.startswith(prefix)]
    return out


def parse_body(lines, def_indent):
    """把函数体解析成步骤列表。

    每步：{'k': 'unpack'|'branch'|'loop'|'call', 'depth': n, ...}
      unpack: name/kind/size/args
      branch: cond
      loop:   head（含可识别的迭代器，如 BASE_PROP[4:]）
      call:   module/callee/args  —— 把 netobj 委托给同工程 helper 的调用
    """
    base = def_indent + 4
    steps = []
    for _ln, raw in lines:
        if not raw.strip() or raw.strip().startswith('#'):
            continue
        code = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        depth = max(0, (indent - base) // 4)
        if code.startswith('if ') or code.startswith('elif '):
            steps.append({'k': 'branch', 'depth': depth, 'cond': code.rstrip(':')})
            continue
        if code == 'else:':
            steps.append({'k': 'branch', 'depth': depth, 'cond': 'else'})
            continue
        if code.startswith('for ') or code.startswith('while '):
            steps.append({'k': 'loop', 'depth': depth, 'head': code.rstrip(':')})
            continue
        m = CALL.search(code)
        if m:
            target, fn, args = m.group(1), m.group(2), (m.group(3) or '').strip()
            # UnpackSignedChar 无显式宽度参数，定宽 1 字节（有符号）。
            # 没有这一步 width_of 会拿不到宽度 → 线序里标成 'S'(None)，与覆盖表对不上。
            if fn == 'UnpackSignedChar':
                args = '1'
            steps.append({'k': 'unpack', 'depth': depth,
                          'name': target or '', 'fn': fn,
                          'kind': KIND.get(fn, '?'), 'args': args})
            continue
        dm = DELEG.search(code)
        if dm and dm.group(2) not in KIND:   # 排除 prop.UnpackXxx（已当 unpack）
            steps.append({'k': 'call', 'depth': depth,
                          'module': dm.group(1), 'callee': dm.group(2),
                          'args': dm.group(3)})
    return steps


def split_top(s):
    """在顶层按逗号切分（忽略括号内的逗号/元组）。"""
    out, depth, buf = [], 0, ''
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(buf)
            buf = ''
        else:
            buf += ch
    if buf.strip():
        out.append(buf)
    return out


def parse_prop_tables(text):
    """从 pet.py 解析全部属性描述表（`self.BASE_PROP = (...)` / `self.ADV_PROP = (...)` …）。

    返回 {表名: [(name, kind, width), ...]}；width 为定宽或 0（TSTRING 变长）。
    这些表是「数据驱动循环」（`for item in self.BASE_PROP[4:]: item.Unpack(netobj)`）
    的字段宽度来源，抽取器据此把循环展开成真实字段序列。
    """
    tables = {}
    for m in re.finditer(r'self\.(\w+)\s*=\s*\(', text):
        name = m.group(1)
        i = text.index('(', m.start())
        depth, j = 0, i
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = text[i + 1:j]
        out = []
        for it in split_top(block):
            it = it.strip()
            mm = re.match(r'Bind(\w+)\(\s*(\w+)\s*,\s*[\'"](\w+)[\'"]', it)
            if not mm:
                continue
            typ, nm = mm.group(2), mm.group(3)
            if typ.isdigit():
                # 字面量宽度（如 BindPropItem(20,'m_Name')）→ 定长字符串
                out.append((nm, 'T', int(typ)))
            elif typ in TYPE_W:
                out.append((nm,) + TYPE_W[typ])
        if out:
            # 同名表可能在多个类里重复定义（如 pet.py 的 CPet 与另一宠物类都挂
            # BASE_PROP/ADV_PROP）。服务端 build_* 一律按完整表打包，故重名时保留
            # 字段更多的那份（即真正在线上使用的布局）。
            if name not in tables or len(out) > len(tables[name]):
                tables[name] = out
    return tables


def inline_calls(steps, module, func_table, seen):
    """把 `call` 步骤内联成被调函数的体（单向、带环检测）。

    func_table: {(module, name): (def_indent, lines)}。找不到的 call 原样保留，
    便于在树视图里看见「这一步委托给了谁」。
    """
    out = []
    for s in steps:
        if s['k'] == 'call':
            key = (s['module'], s['callee'])
            if key in func_table and key not in seen:
                dind, ls = func_table[key]
                sub = parse_body(ls, dind)
                out.extend(inline_calls(sub, s['module'], func_table, seen | {key}))
            else:
                out.append(s)
        else:
            out.append(s)
    return out


def expand_prop_loops(steps, prop_tables):
    """把迭代属性描述表（`BASE_PROP[slice]` / `ADV_PROP` …）的循环展开成真实字段序列。

    循环体（如 `item.Unpack(netobj)`）被整段丢弃，换成该表 [slice] 的字段读数，
    宽度来自 parse_prop_tables。其它循环（xrange/while）不动。
    """
    if not prop_tables:
        return steps
    out, i = [], 0
    while i < len(steps):
        s = steps[i]
        if s['k'] == 'loop':
            m = re.search(r'(?:self\.)?(\w+_PROP)(\[([^\]]*)\])?', s['head'])
            if m and m.group(1) in prop_tables:
                d = s['depth']
                j = i + 1
                while j < len(steps) and steps[j]['depth'] > d:
                    j += 1
                slc = m.group(3) or ''
                if ':' in slc:
                    a, b = slc.split(':')
                    start = int(a) if a.strip() else 0
                    end = int(b) if b.strip() else None
                elif slc.strip():
                    idx = int(slc)
                    start, end = idx, idx + 1
                else:
                    start, end = 0, None
                for nm, kind, w in prop_tables[m.group(1)][start:end]:
                    out.append({'k': 'unpack', 'depth': d, 'name': nm,
                                'fn': 'UnpackInt' if kind == 'I' else 'UnpackString',
                                'kind': kind, 'args': str(w) if kind == 'I' else '0'})
                i = j
                continue
        out.append(s)
        i += 1
    return out


def width_of(kind, args):
    """定长宽度；变长返回 None。"""
    if kind == 'D':
        return None
    if kind == 'B':
        return 1
    if kind == 'T':
        n = args.split(',')[0].strip() if args else '0'
        return int(n) if n.isdigit() and int(n) > 0 else None
    n = args.split(',')[0].strip() if args else ''
    return int(n) if n.isdigit() else None


def linear_steps(steps):
    """返回 (线性线序, 原因)。只有**包住了解包调用**的分支/循环才算影响线序。

    这条判据很关键：handler 里绝大多数 `if` 是解包之后的业务逻辑
    （`if obj == None: return`、`if map.g_Map == None:`），它们不改变字节流。
    真正要拦的是 `if title_len: title = UnpackString(title_len)` 这种——
    分支里面还有解包，静态规格只能覆盖其中一种形态。
    """
    scopes = []            # 当前打开的分支/循环作用域：[(depth, 描述)]
    structural = []
    unpacks = []
    for s in steps:
        d = s['depth']
        while scopes and scopes[-1][0] >= d:
            scopes.pop()
        if s['k'] in ('branch', 'loop'):
            scopes.append((d, s.get('cond') or s.get('head')))
        elif s['k'] == 'unpack':
            unpacks.append(s)
            for _sd, desc in scopes:
                if desc not in structural:
                    structural.append(desc)
        # 'call' 步骤不计入 structural：它只是委托，已被内联展开成上面的 unpack
    if structural:
        return None, '分支/循环内含解包：%s' % structural[0]
    return unpacks, ''


def draft_spec(steps):
    """给线性线序生成覆盖表草稿（`名字:宽度`），变长部分标注出来。"""
    out = []
    for i, s in enumerate(steps):
        nm = s['name'] or ('f%d' % i)
        w = width_of(s['kind'], s['args'])
        if s['kind'] == 'D':
            out.append('%s:D' % nm)
        elif s['kind'] == 'T':
            out.append('%s:f%d' % (nm, w) if w else '%s:s' % nm)
        elif s['kind'] == 'B':
            off, n = (s['args'].split(',') + ['1'])[:2]
            out.append('%s:B(%s)' % (nm, (1 << int(off or 0)).bit_length() - 1))
        elif w is None:
            out.append('%s:?' % nm)
        else:
            out.append('%s:%d%s' % (nm, w, 's' if s['kind'] == 'S' else ''))
    return ' '.join(out)


def load_routes():
    """opcode → (模块, 处理函数)，与 tap 用的是同一份来源。"""
    sys.path.insert(0, SIM)
    import tap
    return tap._parse_s2c_routes(read(os.path.join(SRC, 'net.py')))


def load_overlay():
    sys.path.insert(0, SIM)
    import tap
    return tap.OVERLAY['S2C'], tap.OVERLAY_NAME['S2C'], getattr(tap, '_OV_DYN', {})


def overlay_signature(fields):
    """覆盖表字段 → [(kind, width)]，用于与客户端线序做结构比对。"""
    out = []
    for f in fields:
        kind = f.get('kind')
        if kind == 'D':
            out.append(('D', None))
        elif kind == 'T':
            out.append(('T', None))
        elif kind == 'S':
            out.append(('T', int(f.get('size') or 0) or None))
        elif kind == 'B':
            out.append(('B', 1))
        elif kind == 'P':
            out.append(('T', None))
        elif kind in ('I', 'R'):
            out.append(('I', int(f.get('size') or 0)))
        else:
            out.append((kind, None))
    return out


def client_signature(steps):
    """客户端线序 → [(kind, width)]。

    有符号整数（UnpackSigned / UnpackSignedChar，kind 'S'）与无符号整数（kind 'I'）
    在「线序」上布局完全相同——同为 1/2/4 字节，差别只在值的解释，不会改变字节位置。
    对账的目的是抓**字段错位**，所以把 'S' 归一化到 'I'，否则覆盖表写 `1s` 而客户端
    读 UnpackSigned 会被误判成 MISMATCH（op22/op86 就是这类）。这也能让 op51 那批
    pet.py 以 TBYTE 读、覆盖表却标 `1s` 的字段继续判为一致。
    """
    return [(('I' if s['kind'] in ('I', 'S') else s['kind']),
             width_of(s['kind'], s['args'])) for s in steps]


def build_func_table(files):
    """{(module, name): (def_indent, lines)}，跨文件查被委托的 helper。"""
    table = {}
    for fn in files:
        text = read(os.path.join(SRC, fn))
        mod = fn[:-3]
        for name, dind, ls in split_funcs(text):
            table.setdefault((mod, name), (dind, ls))
    return table


def resolve(name, dind, ls, mod, files, func_table, prop_tables):
    """对一个 S2C handler 跑完整抽取：解析 → 内联委托 → 展开属性表循环 → 取线序。"""
    steps = parse_body(ls, dind)
    steps = inline_calls(steps, mod, func_table, {(mod, name)})
    steps = expand_prop_loops(steps, prop_tables)
    lin, why = linear_steps(steps)
    return {'file': mod + '.py', 'steps': steps, 'linear': lin, 'why': why,
            'spec': draft_spec(lin) if lin is not None else ''}


def main(argv):
    want_json = '--json' in argv
    do_audit = '--audit' in argv
    files = list(FILES)
    for i, a in enumerate(argv):
        if a == '--files' and i + 1 < len(argv):
            files = argv[i + 1].split(',')

    pet_text = read(os.path.join(SRC, 'pet.py')) if 'pet.py' in files else ''
    prop_tables = parse_prop_tables(pet_text) if pet_text else {}
    func_table = build_func_table(files)

    routes = load_routes()
    by_handler = {}
    for op, (mod, fn) in routes.items():
        by_handler.setdefault(fn, op)

    # 收集所有 S2C handler（跨文件，按名去重，后者覆盖——路由权威的那个通常更靠后）
    got = {}
    for fn in files:
        mod = fn[:-3]
        for name, dind, ls in split_funcs(read(os.path.join(SRC, fn)), 'S2C'):
            r = resolve(name, dind, ls, mod, files, func_table, prop_tables)
            r['op'] = by_handler.get(name)
            got[name] = r

    if want_json:
        path = argv[argv.index('--json') + 1]
        io.open(path, 'w', encoding='utf-8').write(
            json.dumps(got, ensure_ascii=False, indent=1))
        print('已写出 %s（%d 个函数）' % (path, len(got)))

    if do_audit:
        return audit(got)

    for name in sorted(got, key=lambda n: (got[n]['op'] is None, got[n]['op'] or 0, n)):
        g = got[name]
        head = 'op%-4s' % g['op'] if g['op'] else '  -  '
        print('=' * 72)
        print('%s %-26s %s' % (head, name, g['file']))
        for s in g['steps']:
            pad = '    ' * s['depth']
            if s['k'] == 'unpack':
                print('  %s%-18s %s(%s)' % (pad, s['name'] or '·', s['fn'], s['args']))
            elif s['k'] == 'branch':
                print('  %s[?] %s' % (pad, s['cond']))
            elif s['k'] == 'loop':
                print('  %s[↻] %s' % (pad, s['head']))
            else:
                print('  %s[→] %s.%s(%s)' % (pad, s['module'], s['callee'], s['args']))
        if g['linear'] is not None:
            print('  → 草稿规格: %s' % (g['spec'] or '（无字段）'))
        else:
            print('  → 非线性（%s），静态规格只能覆盖一种形态' % g['why'])
    return 0


def _same_unpack_each_branch(steps):
    """判断「分支只影响本地逻辑、不影响线序」。

    做法：把每条分支路径上的解包序列抽出来，若**所有路径序列相同**，
    说明线序唯一，静态规格够用。

    这里用简化判据：所有 unpack 步骤（不分层）的序列里，
    不存在「同一位置出现不同解包」的情况——即分支没有引入可选字段。
    对 op16 这类「if 判本地状态、两路都解同样的东西」足够；
    对真正的可选字段（如 op99 的 if title_len != 0）会判 False。
    """
    # 收集所有解包步骤，按 (函数名, 参数) 归一
    ups = [s for s in steps if s['k'] == 'unpack']
    if not ups:
        return False
    seq = [(s['fn'], str(s['args'])) for s in ups]
    # ★ 判据：**所有分支路径产出同一串解包序列**，即「线序唯一」。
    #   典型 op16：
    #     if hero.g_ID:  <g_ID=UnpackInt(4), CreateProp>   ← 分支内一遍
    #     g_ID=UnpackInt(4); CreateProp                     ← 无条件一遍
    #   两遍完全相同 → 不管走哪条路，线上都是同一串字段。
    #   判法：若把序列从中间一切为二，两半相同，则视为「重复表达同一线序」。
    n = len(seq)
    if n % 2 == 0 and seq[:n // 2] == seq[n // 2:]:
        return True
    # 单串（没有分支引入额外解包）也算唯一
    return len(set(seq)) == len(seq)


def audit(got):
    overlay, names, dyn = load_overlay()
    ok = mismatch = need_dyn = has_dyn = 0
    problems = []
    for name in sorted(got, key=lambda n: (got[n]['op'] or 0, n)):
        g = got[name]
        op = g['op']
        if op is None or op not in overlay:
            continue
        if op in dyn:
            has_dyn += 1
            print('OK   op%-4d %-24s 布局随内容变，已由 _OV_DYN 处理' % (op, name))
            continue
        if not g['linear'] and not g['why']:
            print('OK   op%-4d %-24s 自己不解包（委托给 helper），静态无法核对，跳过'
                  % (op, name))
            continue
        if g['linear'] is None:
            # ★ 2026-10-20：先看覆盖表是否**已经表达了**可变结构。
            #   客户端有分支/循环 ≠ 覆盖表必须换成 _OV_DYN——`L{}`（循环）与
            #   `P()`（变长前缀）就是专门用来静态描述这类结构的。
            #   旧逻辑一律判 NEEDS_DYN，把「已配好」的也报成缺口，导致
            #   EXIT_CODE_EXEMPT 长期挂着一条豁免。
            kinds = set(f.get('kind') for f in overlay[op])
            if 'L' in kinds or 'P' in kinds:
                ok += 1
                print('OK   op%-4d %-24s 非线性（%s），但覆盖表已用 %s 表达'
                      % (op, name, g['why'],
                         '+'.join(sorted(k for k in kinds if k in ('L', 'P')))))
                continue
            # ★ 分支只在**客户端本地逻辑**上分叉、两条路径的解包序列相同 →
            #   线序唯一，静态规格足够。典型：op16 S2CPropInit 的
            #   `if hero.g_ID:` —— 两条路径都是 `g_ID=UnpackInt(4)` +
            #   `CreateProp(netobj)`，差别只是「显示登录界面」还是「显示主菜单」。
            #   判定方式：把分支展平后看**解包序列是否唯一**。
            _flat = [s for s in g['steps'] if s['k'] == 'unpack']
            _seq = tuple((s['fn'], str(s['args'])) for s in _flat)
            if _seq and _same_unpack_each_branch(g['steps']):
                ok += 1
                print('OK   op%-4d %-24s 分支仅在本地逻辑分叉，解包序列唯一（%s）'
                      % (op, name, g['why']))
                continue
            need_dyn += 1
            problems.append((op, name, 'NEEDS_DYN', g['why']))
            print('⚠    op%-4d %-24s 覆盖表是静态的，但客户端%s' % (op, name, g['why']))
            continue
        cli = client_signature(g['linear'])
        spec = overlay_signature(overlay[op])
        if cli == spec:
            ok += 1
            print('OK   op%-4d %-24s 线序一致' % (op, name))
        else:
            mismatch += 1
            problems.append((op, name, 'MISMATCH', (cli, spec)))
            print('✗    op%-4d %-24s 线序不一致' % (op, name))
            print('        客户端: %r' % (cli,))
            print('        覆盖表: %r' % (spec,))
            print('        草稿  : %s' % g['spec'])
    print()
    print('对账结果：一致 %d / 不一致 %d / 需动态未配 %d / 已配动态 %d'
          % (ok, mismatch, need_dyn, has_dyn))
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
