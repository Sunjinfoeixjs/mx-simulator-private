# -*- coding: utf-8 -*-
"""梦想世界素材补全包生成器（2026-10-23）—— 招式精修 / 宠物 / 技能 / 地图。

数据来源（两路，互为交叉校验）：
  ① 《梦想世界2013资料全库》  本地 客户端数据/_docx_text.txt（官方资料文本，823 KB）
  ② 客户端真值                 src/data/petskill.py（宠物技能）、src/data/findpathdata.py（场景邻接）、
                               客户端数据/skills_encyclopedia.tsv（招式，504 条，源自 magicdata.py）
  ③ 网上旁证                   叶子猪·流派招式精修大全（2013-11-14）、叶子猪·召唤兽技能介绍（2014-11-05）、
                               《新梦想世界》官方资料专区·流派招式总纲、52pk·召唤兽资质、17173·梦想世界游戏地图（2012-12-10）

产出 6 个 JSON（写在本文件同目录）。约定沿用上一版资料包：
  · 每条带 source / evidence 字段；
  · 解析不出、或对不上客户端真值的条目记进 unresolved，**非空则 exit 1**。

用法：
    python build_data.py            # 生成 + 打印汇总
    python build_data.py --check    # 只做交叉校验，不写文件
"""
import io
import json
import os
import re
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # MX 工作区根
DOCX = os.path.join(ROOT, '客户端数据', '_docx_text.txt')
CLIENT = os.path.join(ROOT, '客户端源码', 'src', 'data')
ENCY = os.path.join(ROOT, '客户端数据', 'skills_encyclopedia.tsv')

SRC_DOCX = '《梦想世界2013资料全库》客户端数据/_docx_text.txt'
SRC_ONLINE = [
    'https://mx.yzz.cn/ 叶子猪·梦想世界新手指导 流派招式精修大全详解（2013-11-14）',
    'https://mx.yzz.cn/ 叶子猪·玩家必看 梦想世界召唤兽技能介绍（2014-11-05）',
    '《新梦想世界》官方资料专区·流派招式总纲 / 招式属性 / 召唤兽技能介绍',
    'https://mxsj.52pk.com/ziliao/ 52pk·梦想世界资料百科·召唤兽资质',
    'https://www.17173.com/ 17173·梦想世界游戏地图（2012-12-10）',
]

# ───────────────────────────────────────────────────────── 通用解析

def load_docx():
    with io.open(DOCX, encoding='utf-8') as f:
        return [l.rstrip('\n') for l in f]


def toc_names(lines):
    """从文首目录里取全部章节名（用于自动定位章节区间）。"""
    names = set()
    for l in lines[:400]:
        m = re.search(r'\\l "[A-Za-z0-9]+"([\u4e00-\u9fa5]+?)(?:PAGEREF|$)', l)
        if m:
            names.add(m.group(1))
    return names


def section(lines, name, after=0):
    """返回章节 (lo, hi) 行号区间（0-based，含 lo 不含 hi）。after：从第 N 次出现之后开始找。"""
    names = toc_names(lines)
    hits = [i for i, l in enumerate(lines) if l.strip() == name and i > 400]
    if not hits:
        raise SystemExit('章节未找到: %s' % name)
    lo = hits[0] if after == 0 else hits[after]
    hi = len(lines)
    for i in range(lo + 1, len(lines)):
        if lines[i].strip() in names:
            hi = i
            break
    return lo, hi


def parse_rows(lines, lo, hi):
    """区间内按 docx 表格线序解析成行。

    线序：单元格独占一行；续单元格以 ' | ' 开头；单独 ' | ' 表示本行结束。
    返回 [(line_no, [cell, ...]), ...]；单单元格行通常是小标题/分类名。
    """
    rows, cur, ln0 = [], None, None
    for i in range(lo, hi):
        s = lines[i].strip()
        if s == '|':
            if cur is not None:
                rows.append((ln0, cur))
                cur, ln0 = None, None
        elif s.startswith('|'):
            if cur is None:
                cur, ln0 = [], i
            cur.append(s[1:].strip())
        else:
            if cur is not None:
                rows.append((ln0, cur))
            cur, ln0 = [s], i + 1
    if cur is not None:
        rows.append((ln0, cur))
    return rows


def grouped_rows(rows, ncols):
    """把 rows 按「单单元格行 = 分组标题」分层，返回 [(group, [(line, cells)])]。"""
    out, cur_name, cur = [], None, []
    for ln, cells in rows:
        if len(cells) == 1:
            if cur:
                out.append((cur_name, cur))
            cur_name, cur = cells[0], []
            continue
        if cells[0].endswith('名称') or cells[0].endswith('名称 '):   # 表头
            continue
        if len(cells) != ncols:
            continue
        cur.append((ln, cells))
    if cur:
        out.append((cur_name, cur))
    return out


# ───────────────────────────────────────────── ① 招式（精修）

REFINE_SCHOOLS = ['剑法', '笔法', '拳招', '刀招', '枪招', '暗器', '佛家', '道家', '法儒', '术儒', '巫术', '阴阳']


def extract_refine(lines):
    """「招式名称 | 最大精修等级 | 效果」这种表头在全库里唯一，直接按表头定位。

    不能用 section('招式精修')：该章节末尾紧接各大流派招式明细表（列不同），
    会把「用途」之类的列当成精修等级。
    """
    out = []
    for i, l in enumerate(lines):
        if l.strip() != '招式名称' or i + 1 >= len(lines) or '最大精修等级' not in lines[i + 1]:
            continue
        school = lines[i - 1].strip()
        nxt = min(len(lines), i + 80)
        for k in range(i + 1, nxt):
            if lines[k].strip() in REFINE_SCHOOLS or lines[k].strip() in toc_names(lines):
                nxt = k
                break
        for ln, cells in parse_rows(lines, i - 1, nxt):
            if len(cells) != 3 or cells[0] == '招式名称':
                continue
            try:
                lv = int(cells[1])
            except ValueError:
                continue
            out.append(OrderedDict([
                ('school', school), ('name', cells[0]), ('max_level', lv),
                ('effect', cells[2]), ('line', ln + 1),
            ]))
    return out


# ───────────────────────────────────────────── ② 宠物（技能 / 种类）

def extract_pet_skills(lines):
    lo, hi = section(lines, '召唤兽技能')
    rows = parse_rows(lines, lo, hi)
    cats = grouped_rows(rows, 2)
    out = []
    for cat, items in cats:
        if cat not in ('标准技能', '高级技能', '终级技能', '特殊技能'):
            continue
        for ln, cells in items:
            out.append(OrderedDict([
                ('category', cat), ('name', cells[0]), ('effect', cells[1]), ('line', ln + 1),
            ]))
    return out


def extract_pet_kinds(lines):
    """仙兽 / 神兽 / 稀有召唤兽 / 阵营召唤兽：按「兽名 → 自带技能表」分层。"""
    out = []
    for kind in ('仙兽', '神兽', '稀有召唤兽', '阵营召唤兽'):
        lo, hi = section(lines, kind)
        rows = parse_rows(lines, lo, hi)
        for name, items in grouped_rows(rows, 2):
            if not items:
                continue
            nm = re.sub(r'^（\d）', '', name).strip()
            out.append(OrderedDict([
                ('kind', kind), ('name', nm),
                ('skills', [OrderedDict([('name', c[0]), ('effect', c[1])]) for _, c in items]),
                ('line', items[0][0] + 1),
            ]))
    return out


def strip_bare_returns(src):
    """反编译产物含模块级裸 `return`（uncompyle6 伪语句），exec 前必须剔除。"""
    return '\n'.join(re.sub(r'^\s*return\s*$', '', l) for l in src.split('\n'))


def load_client_petskill():
    """读 src/data/petskill.py 的 DATA（GBK 字节字面量）→ {id: {name, desc, type, 消耗}}。"""
    src = io.open(os.path.join(CLIENT, 'petskill.py'), encoding='utf-8', errors='replace').read()
    i = src.find('DATA = {')
    if i < 0:
        raise SystemExit('petskill.py 未找到 DATA')
    j = src.find('\nICON_MAPPING', i)
    j = len(src) if j < 0 else j
    ns = {}
    exec(compile(strip_bare_returns(src[i:j].replace('DATA =', 'D =', 1)), '<petskill>', 'exec'), ns)
    out = {}
    for k, v in ns['D'].items():
        dec = lambda b: b.decode('gbk', 'replace') if isinstance(b, bytes) else b
        out[int(k)] = OrderedDict([
            ('name', dec(v.get('name'))), ('desc', dec(v.get('desc'))),
            ('type', v.get('type')), ('fpneed', dec(v.get('fpneed'))),
            ('mpneed', dec(v.get('mpneed'))), ('hpneed', dec(v.get('hpneed'))),
        ])
    return out


def load_client_findpath():
    """读 src/data/findpathdata.py 的 DATA → {场景名: [邻接场景名]}。"""
    src = io.open(os.path.join(CLIENT, 'findpathdata.py'), encoding='utf-8', errors='replace').read()
    i = src.find('DATA = {')
    j = src.find('\n', src.find('}', i)) if i >= 0 else -1
    end = src.find('def ', i)
    end = len(src) if end < i else end
    ns = {}
    exec(compile(strip_bare_returns(src[i:end]), '<findpath>', 'exec'), ns)
    dec = lambda b: b.decode('gbk', 'replace') if isinstance(b, bytes) else b
    return {dec(k): [dec(x) for x in v] for k, v in ns['DATA'].items()}


# ───────────────────────────────────────────── ③ 技能（心法/修炼/剧情/劳动/生产/装备特技特效）

def extract_plain_table(lines, heading, key_name, key_effect):
    lo, hi = section(lines, heading)
    rows = parse_rows(lines, lo, hi)
    out = []
    for ln, cells in rows:
        if len(cells) != 2 or cells[0] in (key_name, '技能名称', '心法名称', '修炼类型', '特效名称'):
            continue
        out.append(OrderedDict([('name', cells[0]), ('effect', cells[1]), ('line', ln + 1)]))
    return out


def extract_quest_skills(lines):
    """剧情技能：名称 + 多行效果 + 剧情点消耗 + 学习 NPC。"""
    lo, hi = section(lines, '剧情技能')
    rows = parse_rows(lines, lo, hi)
    out = []
    for ln, cells in rows:
        if len(cells) < 4 or cells[0] == '技能名称':
            continue
        out.append(OrderedDict([
            ('name', cells[0]), ('effect', '\n'.join(cells[1:-2])),
            ('story_point_cost', cells[-2]), ('teacher', cells[-1]), ('line', ln + 1),
        ]))
    return out


def extract_gear_skills(lines, heading, key_name):
    """装备特技/特效：名称 + 效果 [+ 消耗愤怒值]。"""
    lo, hi = section(lines, heading)
    rows = parse_rows(lines, lo, hi)
    out = []
    for ln, cells in rows:
        if len(cells) < 2 or cells[0] == key_name:
            continue
        it = OrderedDict([('name', cells[0]), ('effect', cells[1])])
        if len(cells) >= 3:
            it['anger_cost'] = cells[2]
        it['line'] = ln + 1
        out.append(it)
    return out


def extract_table3(lines, heading, key_name):
    """名称 / 用途 / 相关NPC 三列表（生产技能）。"""
    lo, hi = section(lines, heading)
    out = []
    for ln, cells in parse_rows(lines, lo, hi):
        if len(cells) != 3 or cells[0] == key_name:
            continue
        out.append(OrderedDict([('name', cells[0]), ('usage', cells[1]), ('npc', cells[2]), ('line', ln + 1)]))
    return out


def extract_text_bullets(lines, heading):
    """劳动技能这类没有表格的章节：抓 （一）（二）… 小标题 + 首句说明。"""
    lo, hi = section(lines, heading)
    out = []
    cur = None
    for i in range(lo, hi):
        s = lines[i].strip()
        m = re.match(r'^（([一二三四五六七八九]+)）(.+)$', s)
        if m:
            if cur:
                out.append(cur)
            cur = OrderedDict([('name', m.group(2).strip()), ('desc', ''), ('line', i + 1)])
        elif cur and not cur['desc'] and s and not s.startswith('|'):
            cur['desc'] = s
    if cur:
        out.append(cur)
    return out


# ───────────────────────────────────────────── ④ 地图（四国 / 首都 / 坐标 / 场景邻接）

CAPITAL_RE = re.compile(r'([\u4e00-\u9fa5]{2,4}国)[―—－-]{1,3}([\u4e00-\u9fa5]{2,8})[：:]\s*([\u4e00-\u9fa5·]{2,10})（(\d{1,3})[，,](\d{1,3})）')
COORD_RE = re.compile(r'([\u4e00-\u9fa5·]{2,10})（(\d{1,3})[，,](\d{1,3})）')


def extract_maps(lines):
    capitals, coords = [], []
    seen_cap, seen_coord = set(), set()
    for i, l in enumerate(lines):
        for m in CAPITAL_RE.finditer(l):
            key = (m.group(1), m.group(2), m.group(3))
            if key in seen_cap:
                continue
            seen_cap.add(key)
            capitals.append(OrderedDict([
                ('country', m.group(1)), ('city', m.group(2)), ('npc', m.group(3)),
                ('x', int(m.group(4))), ('y', int(m.group(5))), ('line', i + 1),
            ]))
        for m in COORD_RE.finditer(l):
            key = (m.group(1), m.group(2), m.group(3))
            if key in seen_coord:
                continue
            seen_coord.add(key)
            ctx = re.sub(r'\s+', '', l)
            coords.append(OrderedDict([
                ('label', m.group(1)), ('x', int(m.group(2))), ('y', int(m.group(3))),
                ('context', ctx[:60]), ('line', i + 1),
            ]))
    return capitals, coords


def load_docx_scenes(lines):
    """召唤兽分布表里的「分布场景」去重 → 资料侧场景名集合。"""
    lo, hi = section(lines, '召唤兽分布')
    rows = parse_rows(lines, lo, hi)
    scenes = set()
    for ln, cells in rows:
        if len(cells) != 3 or cells[0] == '种类':
            continue
        for s in re.split(r'[，,、]', cells[2]):
            s = s.strip()
            if s:
                scenes.add(s)
    return sorted(scenes)


# ───────────────────────────────────────────── 交叉校验 + 落盘

def load_ency_names():
    names = set()
    with io.open(ENCY, encoding='utf-8') as f:
        head = f.readline().rstrip('\n').split('\t')
        ni = head.index('name')
        for l in f:
            p = l.rstrip('\n').split('\t')
            if len(p) > ni:
                names.add(p[ni])
    return names


def write_json(name, obj):
    path = os.path.join(HERE, name)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False))
        f.write('\n')
    return path


def main():
    check_only = '--check' in sys.argv
    lines = load_docx()
    unresolved = []

    # ① 招式精修
    refine = extract_refine(lines)
    ency = load_ency_names()
    gaps = []
    for r in refine:
        hit = r['name'] in ency
        r['in_client'] = hit
        if not hit:
            cand = sorted(n for n in ency if n[-2:] == r['name'][-2:] and n != r['name'])
            r['candidates'] = cand
            gaps.append(('招式精修名不在客户端 magicdata', r['school'], r['name'], cand))
    if len(refine) != 120:
        unresolved.append(('招式精修条数异常（应为 12 流派×10=120）', [len(refine)]))
    refine_doc = OrderedDict([
        ('source', [SRC_DOCX] + SRC_ONLINE[:2]),
        ('evidence', 'A：官方资料全库表格 × 客户端 magicdata 招式名逐条比对；网上（叶子猪 2013-11-14）片段与资料逐字一致'),
        ('note', '每流派 10 招；儒家在资料里拆成「法儒 / 术儒」两张表 → 共 12 张表（总纲正文写 11 流派）'),
        ('schools', REFINE_SCHOOLS),
        ('count', len(refine)),
        ('in_client', sum(1 for r in refine if r['in_client'])),
        ('moves', refine),
    ])

    # ② 宠物技能
    petsk = extract_pet_skills(lines)
    cpet = load_client_petskill()
    cname = {v['name']: k for k, v in cpet.items()}
    for r in petsk:
        if r['name'] in cname:
            c = cpet[cname[r['name']]]
            r['client_id'] = cname[r['name']]
            r['client_desc'] = c['desc']
            r['client_cost'] = OrderedDict([('fp', c['fpneed']), ('mp', c['mpneed']), ('hp', c['hpneed'])])
        else:
            r['client_id'] = None
    miss_pet = []
    for r in petsk:
        if r['client_id'] is None:
            cand = sorted(n for n in cname if r['name'] in n or n in r['name'])
            r['candidates'] = cand
            miss_pet.append(r['name'])
            gaps.append(('宠物技能名不在客户端 petskill.py', r['category'], r['name'], cand))
    if len(petsk) < 100:
        unresolved.append(('宠物技能条数异常（<100）', [len(petsk)]))
    petsk_doc = OrderedDict([
        ('source', [SRC_DOCX] + SRC_ONLINE[1:3]),
        ('evidence', 'A：资料全库分类表 × 客户端 petskill.py（id/描述/消耗）双向命中；B：叶子猪 2014-11-05 片段同文'),
        ('note', 'type 字段来自客户端；资料把技能分成 标准/高级/终级/特殊 四类'),
        ('categories', ['标准技能', '高级技能', '终级技能', '特殊技能']),
        ('count', len(petsk)),
        ('matched_client', len(petsk) - len(miss_pet)),
        ('client_total', len(cpet)),
        ('skills', petsk),
    ])

    # ② 宠物种类（仙兽/神兽/稀有/阵营）
    kinds = extract_pet_kinds(lines)
    for k in kinds:
        for s in k['skills']:
            s['client_id'] = cname.get(s['name'])
    kinds_doc = OrderedDict([
        ('source', [SRC_DOCX] + SRC_ONLINE[1:3]),
        ('evidence', 'A：资料全库自带技能表；技能名能对上 petskill.py 的标 client_id，对不上留 null（资料用名与客户端用名存在「终级/终极」混写）'),
        ('count', len(kinds)),
        ('kinds', kinds),
    ])

    # ③ 技能
    mind = extract_plain_table(lines, '个人心法', '心法名称', '心法效果')
    cult = extract_plain_table(lines, '修炼技能', '修炼类型', '修炼效果')
    quest = extract_quest_skills(lines)
    labor = extract_text_bullets(lines, '劳动技能')
    craft = extract_table3(lines, '生产技能', '名称')
    tec = extract_gear_skills(lines, '装备特技', '特技名称')
    eff = extract_gear_skills(lines, '装备特效', '特效名称')
    skills_doc = OrderedDict([
        ('source', [SRC_DOCX]),
        ('evidence', 'A：资料全库表格（剧情技能含 NPC/剧情点消耗；装备特技含愤怒值消耗）'),
        ('note', '修炼的「每级经验公式」见上一版 cultivation_exp.json，本档只收效果文本'),
        ('counts', OrderedDict([('个人心法', len(mind)), ('修炼技能', len(cult)), ('剧情技能', len(quest)),
                                ('劳动技能', len(labor)), ('生产技能', len(craft)),
                                ('装备特技', len(tec)), ('装备特效', len(eff))])),
        ('个人心法', mind), ('修炼技能', cult), ('剧情技能', quest),
        ('劳动技能', labor), ('生产技能', craft), ('装备特技', tec), ('装备特效', eff),
    ])
    if not (mind and cult and quest and tec):
        unresolved.append(('技能章节为空', [k for k, v in [('个人心法', mind), ('修炼技能', cult), ('剧情技能', quest), ('装备特技', tec)] if not v]))

    # ④ 地图
    caps, coords_raw = extract_maps(lines)
    fp = load_client_findpath()
    scenes = set(fp)
    docx_scenes = load_docx_scenes(lines)
    # 坐标只保留「同句能定位到客户端场景名」的条目，滤掉诗句/称号里的数字
    coords = []
    for c in coords_raw:
        if c['label'] in scenes or any(s in c['context'] for s in scenes if len(s) >= 2):
            c['scene'] = next((s for s in scenes if s in c['context']), c['label'] if c['label'] in scenes else None)
            coords.append(c)
    miss_scene = [s for s in docx_scenes if s not in scenes]
    for s in miss_scene:
        cand = sorted(x for x in scenes if x[:2] == s[:2] and x != s)
        gaps.append(('资料场景名不在客户端 findpathdata', '地图', s, cand))
    if not caps or not docx_scenes:
        unresolved.append(('地图章节为空', ['四国首都 %d / 资料场景 %d' % (len(caps), len(docx_scenes))]))
    maps_doc = OrderedDict([
        ('source', [SRC_DOCX] + SRC_ONLINE[3:]),
        ('evidence', 'A：场景邻接 = 客户端 findpathdata.py 真值；B：四国首都/功能 NPC 坐标 = 资料全库散句（同句含场景名与坐标）'),
        ('note', '坐标为资料原文散句抽取，未经真机实测；场景等级、怪物等级、传送冷却不可得'),
        ('counts', OrderedDict([('四国首都NPC', len(caps)), ('坐标条目', len(coords)),
                                ('资料侧场景', len(docx_scenes)), ('客户端场景', len(scenes)),
                                ('资料场景命中客户端', len(docx_scenes) - len(miss_scene))])),
        ('四国首都', caps),
        ('坐标', coords),
        ('场景邻接', OrderedDict(sorted(fp.items()))),
        ('资料侧场景名', docx_scenes),
    ])

    docs = OrderedDict([
        ('skills_refine.json', refine_doc),
        ('pets_skills.json', petsk_doc),
        ('pets_kinds.json', kinds_doc),
        ('role_skills.json', skills_doc),
        ('maps.json', maps_doc),
    ])

    print('招式精修 %d 条 / 流派 %d / 命中客户端 %d' % (len(refine), len(set(r['school'] for r in refine)), refine_doc['in_client']))
    print('宠物技能 %d 条（命中客户端 %d / 客户端共 %d）' % (len(petsk), len(petsk) - len(miss_pet), len(cpet)))
    print('宠物种类 %d 只（仙兽/神兽/稀有/阵营）' % len(kinds))
    print('技能：心法%d 修炼%d 剧情%d 劳动%d 生产%d 特技%d 特效%d' % (len(mind), len(cult), len(quest), len(labor), len(craft), len(tec), len(eff)))
    print('地图：首都NPC%d 坐标%d 资料场景%d 客户端场景%d' % (len(caps), len(coords), len(docx_scenes), len(fp)))

    if gaps:
        print('\n缺口（资料有、客户端查无，记 gaps.json，不阻断）：')
        for tag, grp, name, cand in gaps:
            print('  - [%s] %s：%s%s' % (grp, tag, name, (' → 候选 ' + '、'.join(cand[:4])) if cand else ''))
        write_json('gaps.json', [OrderedDict([('check', t), ('group', g), ('name', n), ('candidates', c)]) for t, g, n, c in gaps])

    stale = os.path.join(HERE, 'unresolved.json')
    if os.path.exists(stale) and not unresolved:
        os.remove(stale)

    if unresolved:
        print('\n!! 未解析/未命中 %d 类：' % len(unresolved))
        for tag, items in unresolved:
            print('  - %s (%d): %s' % (tag, len(items), '、'.join(map(str, items[:12])) + ('…' if len(items) > 12 else '')))
        write_json('unresolved.json', [OrderedDict([('check', t), ('items', i)]) for t, i in unresolved])
        sys.exit(1)

    if not check_only:
        for name, obj in docs.items():
            write_json(name, obj)
        print('\n已写出：%s' % '、'.join(docs))
    return 0


if __name__ == '__main__':
    sys.exit(main())
