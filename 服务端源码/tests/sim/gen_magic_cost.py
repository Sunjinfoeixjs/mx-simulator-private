# -*- coding: utf-8 -*-
"""生成 magic_cost_data.py（本目录）：

   · MAGIC_COST : {mid: {m_MP, m_FP, m_At, m_Df, m_Dp}}   —— slv=1 快照（兼容旧调用）
   · MAGIC_EXPR : {mid: {mana, fp, at, df, dp, pow, powrate, max_lv, mtype}}
                  —— 客户端 magicdata 的**原始表达式**，供逐级求值
   · MAGIC_INFO : {mid: {name, school, kind, genre, pow, speed, ar, eff, res_*, mainlv}}

来源：
   · src/data/magicdata.py  —— 客户端真实数据源（mana/fp/at/df/dp/pow/powrate 表达式）
   · data/skills_joined.tsv —— 《梦想世界2013资料全库》招式表与 magicdata 按名联立

⚠ 逐级求值口径（与客户端一致，见 src/warui.py:GetMagicInfo / src/block.py）：
     slv  = 招式等级（g_Skills[ST_MAGIC][id].m_Level；未学 = 1）
     gslv = 对应门派基础技能等级（g_Skills[ST_SKILL][SKILLDEPEND[type]].m_Level；未学 = 0）
     slvm = max(0, slv - 2)
   客户端是 **Python 2.6**，表达式里的 `slv/6` 是整除。旧版生成器用 Python 3 的 `/`
   求值，把「群攻」招式的 FP 算大了：1103 在 slv=1 得 26，而 Py2 口径是 25
   （15 + ((1//6)+2-1)*10）。本版用 `//` 复刻 Py2 语义，MAGIC_COST 随之修正。

   同理 at/df/dp 在 magicdata 里**是常量**（不是 slv 表达式），所以「逐级」只体现在
   法力/力道（mana/fp）与威力/效率（pow/powrate）；攻击点/防御点/干扰点是招式固定值。
   这是数据事实，不是漏做。

生成后在文件尾部自检：magic_cost_at(mid, 1) 必须逐项等于 MAGIC_COST[mid]。
"""
import io
import os
import re

SIM = os.path.dirname(os.path.abspath(__file__))


def _find_mx():
    """向上找到 MX 根（含「客户端源码」目录的那层）。

    不写死层数：本工作区的 sim 目录曾整体挪进 `服务端源码/tests/` 一次，
    写死 dirname(dirname(...)) 会指到不存在的 `服务端源码/客户端源码`。
    """
    d = SIM
    for _ in range(5):
        if os.path.isdir(os.path.join(d, '客户端源码')):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(SIM)


MX = _find_mx()
OUT = os.path.join(SIM, 'magic_cost_data.py')
MAGICDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'magicdata.py')
JOIN = os.path.join(MX, '客户端数据', 'skills_joined.tsv')

# src/block.py:1058（客户端真值）：magicdata type → 门派基础技能 id
SKILL_DEPEND = {11: 1001, 12: 1005, 13: 1004, 14: 1002, 15: 1003,
                16: 1011, 21: 1008, 22: 1007, 23: 1006, 24: 1010, 25: 1009}


def unesc(b):
    out = bytearray()
    k = 0
    while k < len(b):
        if b[k] == '\\' and k + 1 < len(b) and b[k + 1] in 'xX':
            out.append(int(b[k + 2:k + 4], 16))
            k += 4
        elif b[k] == '\\' and k + 1 < len(b):
            out.append(ord(b[k + 1]))
            k += 2
        else:
            out.append(ord(b[k]))
            k += 1
    return out.decode('gbk', 'replace')


def py2_expr(expr):
    """客户端 Py2 表达式的整除复刻：`slv/6` → `slv//6`（只动整型字面量分母）。"""
    return re.sub(r'/(\s*\d+)', r'//\1', str(expr))


def ev(expr, slv=1, gslv=0, slvm=0):
    """按客户端 Py2 语义求值一个 magicdata 字段（空/`-` → 0）。"""
    e = py2_expr(expr).strip()
    if e == '' or e == '-':
        return 0
    try:
        return eval(e, {'__builtins__': {}},
                    {'slv': slv, 'gslv': gslv, 'slvm': slvm})
    except Exception:
        return 0


# ── 1) 解析 magicdata ──
mt = io.open(MAGICDATA, encoding='utf-8', errors='replace').read()
matches = list(re.finditer(r"(\d+): \{'type': (\d+),", mt))
magic = {}
for idx, m in enumerate(matches):
    mid = int(m.group(1))
    mtype = int(m.group(2))
    start = m.start()
    end = matches[idx + 1].start() if idx + 1 < len(matches) else len(mt)
    b = mt[start:end]

    def fld(key):
        mm = re.search(r"'%s':\s*(?:b'((?:[^'\\]|\\.)*)'|([^,\n]+))" % key, b)
        if not mm:
            return None
        return unesc(mm.group(1)) if mm.group(1) is not None else mm.group(2).strip().strip("'\"")

    magic[mid] = {'type': mtype, 'name': fld('name'), 'mana': fld('mana'),
                  'fp': fld('fp'), 'at': fld('at'), 'df': fld('df'), 'dp': fld('dp'),
                  'pow': fld('pow'), 'powrate': fld('powrate'),
                  'max_lv': fld('max_lv'), 'speed': fld('speed'), 'ar': fld('ar'),
                  'major': fld('major'), 'minor': fld('minor')}

# ── 2) 资料库联立表（school/kind/genre/...）──
doc = {}
if os.path.exists(JOIN):
    with io.open(JOIN, encoding='utf-8') as f:
        head = f.readline().rstrip('\n').split('\t')
        for ln in f:
            v = ln.rstrip('\n').split('\t')
            r = dict(zip(head, v))
            if r.get('mid'):
                doc[int(r['mid'])] = r

# ── 3) 组装 ──
COST_FIELDS = ('mana', 'fp', 'at', 'df', 'dp')
MAGIC_COST = {}
MAGIC_EXPR = {}
MAGIC_INFO = {}
for mid, v in magic.items():
    MAGIC_COST[mid] = {'m_MP': int(ev(v['mana'])), 'm_FP': int(ev(v['fp'])),
                       'm_At': int(ev(v['at'])), 'm_Df': int(ev(v['df'])),
                       'm_Dp': int(ev(v['dp']))}
    MAGIC_EXPR[mid] = {
        'mana': str(v['mana'] if v['mana'] is not None else '0'),
        'fp': str(v['fp'] if v['fp'] is not None else '0'),
        'at': str(v['at'] if v['at'] is not None else '0'),
        'df': str(v['df'] if v['df'] is not None else '0'),
        'dp': str(v['dp'] if v['dp'] is not None else '0'),
        'pow': str(v['pow'] if v['pow'] is not None else '0'),
        'powrate': str(v['powrate'] if v['powrate'] is not None else '0'),
        'max_lv': int(re.sub(r'\D', '', str(v['max_lv'] or '1')) or 1),
        'mtype': int(v['type']),
        'base_skill': SKILL_DEPEND.get(int(v['type']), 0),
    }
    info = {'name': v['name'] or ''}
    if mid in doc:
        d = doc[mid]
        info.update({'school': d.get('school', '').replace('招式', ''),
                     'kind': d.get('kind', ''), 'genre': d.get('genre', ''),
                     'pow': d.get('pow', ''), 'speed': d.get('speed', ''),
                     'ar': d.get('ar', ''), 'eff': d.get('eff', ''),
                     'res_lidi': d.get('res_lidi', ''),
                     'res_lingqiao': d.get('res_lingqiao', ''),
                     'res_qungong': d.get('res_qungong', ''),
                     'res_putong': d.get('res_putong', ''),
                     'mainlv': d.get('mainlv', '')})
    MAGIC_INFO[mid] = info

HEADER = '''# -*- coding: utf-8 -*-
"""招式消耗 / 威力 / 效率表（自动生成，勿手改）。

生成：python tests/sim/gen_magic_cost.py（数据源 src/data/magicdata.py + skills_joined.tsv）

三张表：
  · MAGIC_COST  {mid: {m_MP,m_FP,m_At,m_Df,m_Dp}}  —— **slv=1 快照**，兼容旧调用
  · MAGIC_EXPR  {mid: 原始表达式 + max_lv/mtype/base_skill}} —— 逐级求值用
  · MAGIC_INFO  {流派/类别/威力/效率/四抗性/主修等级} —— 面板与战斗语义（**1 级**数值）

逐级求值口径（与客户端 warui.GetMagicInfo / block.py 一致）：
    slv  = 招式等级（未学 = 1）
    gslv = 对应门派基础技能等级（SKILL_DEPEND[type]，未学 = 0）
    slvm = max(0, slv - 2)

⚠ 客户端是 Python 2.6 → 表达式里的 `slv/6` 是**整除**。旧版生成器用 Python 3 的 `/`
  求值，把群攻招式 1103 的 1 级 FP 算成 26（Py2 口径 25）。本版复刻 Py2 整除。

⚠ at/df/dp 在 magicdata 里是**常量**：所以「逐级」只体现在法力/力道与威力/效率，
  攻击点/防御点/干扰点是招式固定值（数据事实）。

只依赖标准库；求值函数在文件后半段，供 m4_server / 测试直接 import。
"""
import re as _re

'''

TAIL = '''

# ══════════════════════════════════════════════════════════════════════════
# 求值函数（纯函数，无副作用；口径见文件头）
# ══════════════════════════════════════════════════════════════════════════
_INT_DIV = _re.compile(r'/(\\s*\\d+)')


def py2_expr(expr):
    """复刻客户端 Py2 整除：只把「分母为整型字面量」的 `/` 换成 `//`。

    例：`15+(slv-1)*1+(slv/6+2-1)*10` → `15+(slv-1)*1+(slv//6+2-1)*10`
    只动字面量分母是刻意的：`gslv*0.15` 这类浮点系数不受影响。
    """
    return _INT_DIV.sub(r'//\\1', str(expr))


def magic_value_at(mid, field, slv=1, gslv=0, slvm=None):
    """按招式等级求 MAGIC_EXPR[mid][field]（空/`-` → 0）。"""
    exprs = MAGIC_EXPR.get(int(mid))
    if not exprs:
        return 0
    raw = exprs.get(field)
    if raw is None:
        return 0
    if slvm is None:
        slvm = max(0, int(slv) - 2)
    e = py2_expr(raw).strip()
    if e in ('', '-'):
        return 0
    try:
        return eval(e, {'__builtins__': {}},
                    {'slv': int(slv), 'gslv': int(gslv), 'slvm': int(slvm)})
    except Exception:
        return 0


def magic_cost_at(mid, slv=1, gslv=0, xinfarate=1.0, slvm=None):
    """逐级施法消耗 → {m_MP,m_FP,m_At,m_Df,m_Dp}（整数）。

    客户端的取整方式（warui.GetMagicInfo）：
        mp = eval(mana)        （心法 3002 时才 * xinfarate 并 int）
        fp = int(eval(fp) * xinfarate) + 精修加成
        at/df/dp = int(raw * xinfarate)
    这里统一 int() 截断；xinfarate 默认 1.0（本服没有心法/精修系统）。
    """
    r = float(xinfarate)
    return {
        'm_MP': int(float(magic_value_at(mid, 'mana', slv, gslv, slvm)) * r),
        'm_FP': int(float(magic_value_at(mid, 'fp', slv, gslv, slvm)) * r),
        'm_At': int(float(magic_value_at(mid, 'at', slv, gslv, slvm)) * r),
        'm_Df': int(float(magic_value_at(mid, 'df', slv, gslv, slvm)) * r),
        'm_Dp': int(float(magic_value_at(mid, 'dp', slv, gslv, slvm)) * r),
    }


def magic_pow_at(mid, slv=1, gslv=0):
    """招式的「威力」：客户端 warui 面板 eval(data['pow']) 的同源求值。"""
    return int(magic_value_at(mid, 'pow', slv, gslv))


def magic_powrate_at(mid, slv=1, gslv=0):
    """招式的「效率」（powrate，百分数）：warui 面板 eval(data['powrate'])。"""
    return int(magic_value_at(mid, 'powrate', slv, gslv))


def magic_max_level(mid):
    return int((MAGIC_EXPR.get(int(mid)) or {}).get('max_lv', 1) or 1)
'''

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write(HEADER)
    f.write('MAGIC_COST = {\n')
    for mid in sorted(MAGIC_COST):
        c = MAGIC_COST[mid]
        f.write("    %d: {'m_MP': %d, 'm_FP': %d, 'm_At': %d, 'm_Df': %d, 'm_Dp': %d},\n"
                % (mid, c['m_MP'], c['m_FP'], c['m_At'], c['m_Df'], c['m_Dp']))
    f.write('}\n\n# MAGIC_COST 键含义：{m_MP, m_FP, m_At, m_Df, m_Dp}'
            '（slv=1,gslv=0 的快照；逐级见 magic_cost_at）\n\n')
    f.write('MAGIC_EXPR = {\n')
    for mid in sorted(MAGIC_EXPR):
        e = MAGIC_EXPR[mid]
        f.write("    %d: {'mana': %r, 'fp': %r, 'at': %r, 'df': %r, 'dp': %r,\n"
                % (mid, e['mana'], e['fp'], e['at'], e['df'], e['dp']))
        f.write("          'pow': %r, 'powrate': %r, 'max_lv': %d, 'mtype': %d,"
                " 'base_skill': %d},\n"
                % (e['pow'], e['powrate'], e['max_lv'], e['mtype'], e['base_skill']))
    f.write('}\n\n')
    f.write('MAGIC_INFO = {\n')
    for mid in sorted(MAGIC_INFO):
        i = MAGIC_INFO[mid]
        f.write("    %d: {'name': %r, 'school': %r, 'kind': %r, 'genre': %r,\n"
                % (mid, i.get('name', ''), i.get('school', ''), i.get('kind', ''), i.get('genre', '')))
        f.write("          'pow': %r, 'speed': %r, 'ar': %r, 'eff': %r, 'mainlv': %r,\n"
                % (i.get('pow', ''), i.get('speed', ''), i.get('ar', ''), i.get('eff', ''), i.get('mainlv', '')))
        f.write("          'res_lidi': %r, 'res_lingqiao': %r, 'res_qungong': %r, 'res_putong': %r},\n"
                % (i.get('res_lidi', ''), i.get('res_lingqiao', ''), i.get('res_qungong', ''), i.get('res_putong', '')))
    f.write('}\n')
    f.write(TAIL)

# ── 4) 写后自检：slv=1 快照必须与函数求值逐项一致 ──
import importlib.util

spec = importlib.util.spec_from_file_location('_mcd_check', OUT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
bad = []
for mid, snap in mod.MAGIC_COST.items():
    got = mod.magic_cost_at(mid, 1)
    if got != snap:
        bad.append((mid, snap, got))
if bad:
    print('✗ 自检失败：magic_cost_at(mid,1) 与 MAGIC_COST 不一致 %d 条' % len(bad))
    for row in bad[:10]:
        print('   ', row)
    raise SystemExit(1)

print('MAGIC_COST 条目 = %d，MAGIC_EXPR 条目 = %d，MAGIC_INFO 条目 = %d'
      % (len(mod.MAGIC_COST), len(mod.MAGIC_EXPR), len(mod.MAGIC_INFO)))
print('样例 1101 slv=1 :', mod.magic_cost_at(1101, 1),
      'pow=%d eff=%d' % (mod.magic_pow_at(1101, 1), mod.magic_powrate_at(1101, 1)))
print('样例 1103 slv=1/6:', mod.magic_cost_at(1103, 1), mod.magic_cost_at(1103, 6))
print('样例 2101 slv=1/6:', mod.magic_cost_at(2101, 1), mod.magic_cost_at(2101, 6))
print(' ->', OUT)
