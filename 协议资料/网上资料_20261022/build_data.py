# -*- coding: utf-8 -*-
"""把「网上/官方资料」整理成可校验的 JSON 数据包。

产物（同目录）：
  pets_carry.json        召唤兽携带等级（77 条）
  quests_mainline.json   主线剧情任务表（23 条）
  scene_encounters.json  场景遇怪表（43 场景 / 124 条怪物，与 sim/encounter_data.py 同源）
  cultivation_exp.json   八种修炼的经验表 + 公式（真值）
  alchemy_recipes.json   炼药配方（玩家实测，部分材料名在客户端 itemdata 中不存在 → 标 UNRESOLVED）
  boss_hp.json           江湖志 1-8 关 / 三杀兽 / 剧情 boss 血量（半真值，攻略实测口径）

来源与可信度见 README.md；每条数据在 JSON 里带 source 字段。

用法：python build_data.py
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(HERE))
DOCX = os.path.join(MX, '客户端数据', '_docx_text.txt')
ITEMDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'itemdata.py')
SIM = os.path.join(MX, '服务端源码', 'sim')

SRC_DOCX = '《梦想世界2013资料全库》（客户端数据/_docx_text.txt）'
SRC_YZZ_PET = 'https://mx.yzz.cn/call/2010,100673.shtml（叶子猪端游资料站·召唤兽分布，2010-01-15）'
SRC_52PK_QUEST = 'https://mxsj.52pk.com/jingyan/6146254.shtml（52pk·剧情任务一览表）'
SRC_52PK_XL = 'https://mxsj.52pk.com/xinqing/519630.shtml + https://wen.baidu.com/question/408281816.html（两源同式）'
SRC_52PK_DRUG = 'https://mxsj.52pk.com/xinqing/5424202.shtml / 5424203.shtml（玩家实测配方）'
SRC_DOYO_HP = 'https://doyo.cn/article/6865（江湖志 1-8 关怪物血量）'
SRC_52PK_HP = 'https://mxsj.52pk.com/jingyan/5500606_2.shtml / _4.shtml（天界之路 boss 血量）'


def load_module_data(path, var='DATA'):
    """加载反编译数据表：容忍模块级裸 return，名称 bytes 按 GBK 解码。

    优先 exec（monsterdata 这类可执行文件）；itemdata.py 里混有 Python2 的
    `print 'x', y` 语句，exec 会 SyntaxError —— 此时退化为「只抠 DATA 字面量」。
    """
    src = io.open(path, encoding='utf-8', errors='ignore').read()
    fixed = []
    for ln in src.split('\n'):
        if ln.strip() == 'return':
            # 保留缩进：模块级 return 改成同缩进的 pass，函数内 return 也一并安全化
            fixed.append(ln[:len(ln) - len(ln.lstrip())] + 'pass')
        else:
            fixed.append(ln)
    ns = {}
    try:
        exec('\n'.join(fixed), ns)
        return ns[var]
    except SyntaxError:
        pass

    # 退化路径：抠出 `VAR = {...}` 的括号平衡字面量，再用 literal_eval
    start = src.find('%s = {' % var)
    if start < 0:
        raise RuntimeError('找不到 %s = {...}' % var)
    i = src.find('{', start)
    depth = 0
    quote = None
    esc = False
    j = i
    while j < len(src):
        ch = src[j]
        if quote:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == quote:
                quote = None
        else:
            if ch in ('"', "'"):
                quote = ch
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
        j += 1
    import ast
    return ast.literal_eval(src[i:j + 1])


def parse_pets(text):
    i = text.find('携带各类召唤兽所要求的最低等级如下')
    j = text.find('以下列出各场景对应的宠物分布', i)
    seg = text[i:j]
    rows = re.findall(r'\n([^\n|]{1,12})\n \| (\d+)\n \| ([^\n]*)\n \|', seg)
    out = []
    for name, lv, scenes in rows:
        raw = name
        rare = '（稀有）' in name or '(稀有)' in name
        name = re.sub(r'[（(]稀有[）)]', '', name)
        out.append({
            'name': name,
            'carry_level': int(lv),
            'scenes': [s for s in re.split(r'[、，,]', scenes.strip()) if s],
            'rare': rare,
            'raw': raw,
            'source': SRC_DOCX + ' × ' + SRC_YZZ_PET,
        })
    return out


def parse_quests(text):
    k = text.find('剧情任务一览表')
    seg = text[k:k + 5000]
    rows = re.findall(r'\n\s*(\d+)\s*\| ([^|\n]+)\s*\| ([^|\n]+)\s*\| ([^|\n]+)\s*\| ([^|\n]+)\s*\| (\d+)', seg)
    out = [{
        'no': int(no), 'name': name.strip(), 'npc': npc.strip(),
        'map': mp.strip(), 'require': req.strip(), 'story_point': int(sp),
        'source': SRC_DOCX,
    } for (no, name, npc, mp, req, sp) in rows]
    # 线上补两条（资料全库只到 22；52pk 的 23 条版本多出 22/23）
    have = {q['name'] for q in out}
    extra = [
        ('奇迹的方舟', '神武尊', '天空要塞', '≥125（或飞升后≥120）并完成之前所有剧情', 0),
        ('黎明的曙光', '神武尊', '梵天古城', '≥130（或飞升后≥125）并完成之前所有剧情', 0),
    ]
    for name, npc, mp, req, sp in extra:
        if name not in have:
            out.append({'no': len(out) + 1, 'name': name, 'npc': npc, 'map': mp,
                        'require': req, 'story_point': sp, 'source': SRC_52PK_QUEST})
    return out


def cultivation_table():
    atk, dfn = [], []
    for n in range(30):
        a = 411 + n * 177.179 + n * (n - 1) / 2.0 * 68.0559 + n * (n - 1) * (n - 2) / 6.0 * 2.89
        d = 274 + n * 117.855 + n * (n - 1) / 2.0 * 45.4319 + n * (n - 1) * (n - 2) / 6.0 * 1.921
        atk.append(int(round(a)))
        dfn.append(int(round(d)))
    return atk, dfn


def parse_item_names():
    data = load_module_data(ITEMDATA)
    out = {}
    for shape, row in data.items():
        nm = row[0] if isinstance(row, (tuple, list)) else None
        if isinstance(nm, bytes):
            nm = nm.decode('gbk', 'replace')
        if nm:
            out[nm] = int(shape)
    return out


ALCHEMY = [
    # (产物, [(材料, 数量)...], 说明)
    ('凝神散', [('隔山香', 5), ('四叶草', 2), ('当归', 3)], '品质18 气血540 魔法81'),
    ('小还丹', [('柴胡', 1), ('甘草', 4), ('沙棘豆', 3)], '品质15 气血585'),
    ('舒筋活络丸', [('柴胡', 1), ('灵芝草', 3), ('牛藤', 3)], '品质15 气血525 力道78'),
    ('舒筋活络丸', [('护心草', 3), ('灵芝草', 3), ('牛藤', 3)], '同产物另一组合'),
    ('铁血归元丹', [('护心草', 2), ('灵芝草', 3), ('牛藤', 2)], '品质15 气血915'),
    ('铁血', [('当归', 5), ('何首乌', 5), ('杜仲', 5)], '第二篇'),
    ('定心', [('千金藤', 3), ('过山龙', 3), ('血藤', 3)], '第二篇'),
    ('回命', [('苍术', 3), ('满山红', 5), ('舒筋草', 4)], '第二篇'),
    ('大力', [('知母', 3), ('何首乌', 3), ('甘草', 3)], '第二篇'),
    ('聚神', [('华山参', 3), ('血藤', 3), ('灵芝草', 3)], '第二篇'),
    ('疑神', [('护心草', 10), ('舒筋草', 10), ('华山参', 10)], '第二篇'),
    ('金辉', [('干草', 10), ('千金藤', 10), ('半血莲', 10)], '第二篇'),
    ('返生', [('杀刺豆', 10), ('血藤', 10), ('过山龙', 10)], '第二篇'),
]


def similar_names(name, names):
    """给「攻略里的材料名在 itemdata 查无此名」找疑似错字候选（只提示，不自动采用）。"""
    out = []
    for n in names:
        if n == name or abs(len(n) - len(name)) > 1:
            continue
        same = len(set(n) & set(name))
        score = 0
        if n[-1] == name[-1]:
            score += 2
        if n[0] == name[0]:
            score += 1
        score += same
        if score >= 3 or (len(n) == len(name) and same >= 2):
            out.append((score, n))
    return [n for _s, n in sorted(out, reverse=True)[:5]]


def alchemy():
    ids = parse_item_names()
    out = []
    for prod, mats, note in ALCHEMY:
        row = {'product': prod, 'materials': [], 'note': note, 'source': SRC_52PK_DRUG}
        missing = []
        for name, qty in mats:
            sid = ids.get(name)
            item = {'name': name, 'qty': qty,
                    'shape': sid, 'status': 'OK' if sid else 'UNRESOLVED'}
            if not sid:
                missing.append(name)
                item['candidates'] = similar_names(name, ids)
            row['materials'].append(item)
        row['status'] = 'UNRESOLVED' if missing else 'OK'
        row['missing'] = missing
        out.append(row)
    return out


def boss_hp():
    return {
        'source': [SRC_DOYO_HP, SRC_52PK_HP],
        'confidence': '半真值：攻略作者实测/目测，血量是「左右」口径，不保证精确',
        'entries': [
            {'name': '兔战士', 'hp': 1800, 'where': '江湖志第1关', 'source': SRC_DOYO_HP},
            {'name': '绿晶蜥', 'hp': 2000, 'where': '江湖志第2关', 'source': SRC_DOYO_HP},
            {'name': '小魔女', 'hp': 2200, 'where': '江湖志第3关', 'source': SRC_DOYO_HP},
            {'name': '牛妖', 'hp': 2600, 'where': '江湖志第4关', 'source': SRC_DOYO_HP},
            {'name': '桃花仙子', 'hp': 2900, 'where': '江湖志第5关', 'source': SRC_DOYO_HP},
            {'name': '兔天使', 'hp': 3000, 'where': '江湖志第6关', 'source': SRC_DOYO_HP},
            {'name': '翼虎', 'hp': 3000, 'where': '江湖志第7关', 'source': SRC_DOYO_HP},
            {'name': '死亡骑士', 'hp': None, 'where': '江湖志第8关', 'source': SRC_DOYO_HP,
             'note': '原文「不详」'},
            {'name': '三杀兽·牙', 'hp': 7000, 'where': '天界之路·力量之兽', 'source': SRC_52PK_HP},
            {'name': '牙·兽', 'hp': 6000, 'where': '天界之路·小怪', 'source': SRC_52PK_HP},
            {'name': '三杀兽·角', 'hp': None, 'where': '天界之路·智慧之兽', 'source': SRC_52PK_HP,
             'note': '原文未给血量（「生命与伙伴同在」）'},
            {'name': '三杀兽·鳞', 'hp': 15000, 'where': '天界之路·勇气之兽', 'source': SRC_52PK_HP},
            {'name': '鳞·兽', 'hp': 10000, 'where': '天界之路·小怪', 'source': SRC_52PK_HP},
        ],
    }


def scene_encounters():
    sys.path.insert(0, SIM)
    import encounter_data as ed
    out = []
    for mid in sorted(ed.MAP_ENCOUNTERS):
        e = ed.MAP_ENCOUNTERS[mid]
        out.append({'mapid': mid, 'scene': e['scene'], 'scene_level': e['level'],
                    'monsters': [{'name': n, 'shape': s} for n, s in e['monsters']]})
    return {'source': [SRC_DOCX + '（场景表）× 客户端 monsterdata.py', SRC_YZZ_PET.replace('召唤兽分布', '召唤兽分布·场景表')],
            'note': '场景等级不是怪物等级（资料原文）；每场数量/权重/HP 不可得',
            'scenes': out}


def dump(name, obj):
    path = os.path.join(HERE, name)
    io.open(path, 'w', encoding='utf-8').write(
        json.dumps(obj, ensure_ascii=False, indent=1) + '\n')
    print('written %-24s %d bytes' % (name, os.path.getsize(path)))


def main():
    text = io.open(DOCX, encoding='utf-8', errors='ignore').read()

    pets = parse_pets(text)
    check = [p for p in pets if p['name'] == '雷光兽']
    print('pets=%d (雷光兽 carry=%s)' % (len(pets), check[0]['carry_level'] if check else '?'))
    dump('pets_carry.json', {
        'source': [SRC_DOCX, SRC_YZZ_PET],
        'confidence': '真值（官方资料表，线上同表逐条一致；线上页缺 10 条，以资料全库 77 条为准）',
        'count': len(pets), 'pets': pets})

    quests = parse_quests(text)
    print('quests=%d' % len(quests))
    dump('quests_mainline.json', {
        'source': [SRC_DOCX, SRC_52PK_QUEST],
        'confidence': '真值（表结构/触发NPC/场景/等级门槛/剧情点）；剧情流程细节另见攻略',
        'count': len(quests), 'quests': quests})

    atk, dfn = cultivation_table()
    print('cultivation 0->1 %d/%d  29->30 %d/%d' % (atk[0], dfn[0], atk[-1], dfn[-1]))
    dump('cultivation_exp.json', {
        'source': SRC_52PK_XL,
        'confidence': '真值（公式由两处独立来源给出同一形式；表由公式重算，0→1 与 29→30 与原文逐位一致）',
        'formula': {
            'attack_magic': '411 + n*177.179 + n*(n-1)/2*68.0559 + n*(n-1)*(n-2)/6*2.89',
            'defense_resist_command': '274 + n*117.855 + n*(n-1)/2*45.4319 + n*(n-1)*(n-2)/6*1.921',
        },
        'types': ['攻击修炼', '法术修炼', '防御修炼', '抗法修炼',
                  '攻击指挥修炼', '防御指挥修炼', '法术指挥修炼', '抗法指挥修炼'],
        'cap': {'normal': 30, 'ascended': 35},
        'per_level': {'attack_magic': atk, 'defense_resist_command': dfn},
    })

    recipes = alchemy()
    ok = [r for r in recipes if r['status'] == 'OK']
    print('alchemy=%d ok=%d unresolved=%d' % (len(recipes), len(ok), len(recipes) - len(ok)))
    dump('alchemy_recipes.json', {
        'source': SRC_52PK_DRUG,
        'confidence': '玩家实测（非官方）：产物/比例可信度中；4 个材料名在客户端 itemdata 中查无此名（疑为攻略错字），标 UNRESOLVED',
        'count': len(recipes), 'recipes': recipes})

    dump('boss_hp.json', boss_hp())
    dump('scene_encounters.json', scene_encounters())
    return 0


if __name__ == '__main__':
    sys.exit(main())
