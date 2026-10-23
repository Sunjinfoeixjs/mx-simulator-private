# -*- coding: utf-8 -*-
"""生成 tools/sim/drop_data.py：按场景的掉落池（真值表）。

来源（两条真值，无手写内容）：
  · `客户端数据/_docx_text.txt` —— 《梦想世界2013资料全库》「暗雷/明雷场景」表
    （列：场景名称 | 场景等级 | 怪物名称 | 掉落物品；115 级以上 4 行多一列「稀有宠物」）
  · `客户端源码/src/data/itemdata.py` —— 客户端真实物品表（GBK bytes，名称 + 描述内的
    【类型】/【等级】字段），用于把掉落物名解析为真实 item id

规则：
  · 「N级武器 / N级防具」是**类目**，不是具体物品 → 解析为该等级该类目的全部真实物品 id
  · 「凡品晶矿」同理，对应 itemdata 里 `…晶矿·凡品` 一族
  · 其余掉落物名按物品名精确匹配
  · 解析不出来的 token 一律进 UNRESOLVED，不猜、不编 id

判断口径（可改，改完重跑即可）：
  · 武器  = 【类型】武器
  · 防具  = 【类型】∈{铠甲, 帽子, 鞋子, 腰带, 护腕}（身体装备）
            项链/戒指/法宝 属饰品与法宝，资料表未单列，故不计入防具

用法：
    python gen_drop_data.py            # 重新生成 drop_data.py + drop_resolve_report.tsv
"""
import io
import os
import re

MX = r'C:\Users\Administrator\MX'
DOCX = os.path.join(MX, '客户端数据', '_docx_text.txt')
ITEMDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'itemdata.py')
OUT = os.path.join(MX, '服务端源码', 'sim', 'drop_data.py')
REPORT = os.path.join(MX, '服务端源码', 'sim', 'drop_resolve_report.tsv')

# 类目口径：资料表里的「N级武器 / N级防具」落到 itemdata 的【类型】
CATEGORY_KINDS = {
    u'武器': (u'武器',),
    u'防具': (u'铠甲', u'帽子', u'鞋子', u'腰带', u'护腕'),
}
# 资料表里的专有类目（不是单一物品名）
SPECIAL_CATEGORY = {
    u'凡品晶矿': lambda name: (u'晶矿' in name and name.endswith(u'·凡品')),
}


def unesc(b):
    """itemdata 里的 b'\\xxx' 字面量 → GBK 文本。"""
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


# ── 1) 解析资料全库的场景表（暗雷 + 明雷两段） ────────────────────────────────
def parse_tables():
    lines = io.open(DOCX, encoding='utf-8', errors='replace').read().split('\n')
    rows = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != '场景名称':
            i += 1
            continue
        # 跳过表头四格（场景名称/场景等级/怪物名称/掉落物品）
        i += 1
        cells = []
        while i < len(lines):
            s = lines[i].strip()
            i += 1
            if s in ('', '|'):
                continue
            if s.startswith('|'):
                s = s.lstrip('|').strip()
                if not s:
                    continue
            if s.endswith('：') or s.startswith('召唤兽') or s == '场景名称':
                break
            cells.append(s)
        # 四格一组；若第 4 格全是「（稀有）」宠物，则它是额外的稀有宠物列，掉落顺延
        p = 0
        while p + 3 < len(cells):
            scene, lvl, mons, drop = cells[p], cells[p + 1], cells[p + 2], cells[p + 3]
            p += 4
            rare = ''
            if re.fullmatch(r'(?:[^、]+（稀有）、)*[^、]+（稀有）', drop) and p < len(cells):
                rare, drop = drop, cells[p]
                p += 1
            if not re.fullmatch(r'\d+', lvl):
                p = p - 3          # 错位，回退重试
                continue
            rows.append((scene, int(lvl), mons, drop, rare))
    return rows


# ── 2) 解析客户端物品表：名称→id、类别/等级 ────────────────────────────────────
def parse_items():
    text = io.open(ITEMDATA, encoding='utf-8', errors='replace').read()
    pat = re.compile(r"(\d+):\s*\(\s*b'((?:[^'\\]|\\.)*)'\s*,\s*b'((?:[^'\\]|\\.)*)'", re.S)
    items = {}
    for m in pat.finditer(text):
        iid = int(m.group(1))
        items[iid] = (unesc(m.group(2)), unesc(m.group(3)))
    return items


def kind_level(desc):
    """从描述里取 (类型, 等级)。类型形如「武器：剑」→ 归一为「武器」。"""
    kind = ''
    lv = None
    m = re.search(r'【类型】\s*([^#\n]{1,16})', desc)
    if m:
        kind = re.split(u'[：:]', m.group(1).strip())[0].strip()
    m = re.search(r'【等级】\s*(\d+)', desc)
    if m:
        lv = int(m.group(1))
    return kind, lv


def main():
    rows = parse_tables()
    items = parse_items()

    by_name = {}
    by_kind_level = {}
    for iid, (name, desc) in items.items():
        by_name.setdefault(name, []).append(iid)
        kind, lv = kind_level(desc)
        if kind and lv is not None:
            by_kind_level.setdefault((kind, lv), []).append(iid)

    data = {}
    unresolved = []
    report = []
    for scene, lvl, mons, drop, rare in rows:
        pools = []
        for token in [t.strip() for t in drop.split(u'、') if t.strip()]:
            m = re.fullmatch(u'(\\d+)级(武器|防具)', token)
            if m:
                kinds = CATEGORY_KINDS[m.group(2)]
                lv = int(m.group(1))
                ids = sorted(i for k in kinds for i in by_kind_level.get((k, lv), []))
                hit = [(i, items[i][0]) for i in ids]
                kind_label = u'类目'
            elif token in SPECIAL_CATEGORY:
                pred = SPECIAL_CATEGORY[token]
                ids = sorted(i for i, (nm, _d) in items.items() if pred(nm))
                hit = [(i, items[i][0]) for i in ids]
                kind_label = u'类目'
            else:
                ids = sorted(by_name.get(token, []))
                hit = [(i, items[i][0]) for i in ids]
                kind_label = u'名称'
            if not hit:
                unresolved.append((scene, token))
                continue
            pools.append((token, kind_label, hit))
            report.append((scene, str(lvl), token, kind_label, str(len(hit)),
                           ','.join(names for _, names in hit[:6])))
        data.setdefault(scene, {'level': lvl, 'monsters': mons.split(u'、'),
                                'rare': [x for x in rare.split(u'、') if x],
                                'pools': pools})

    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(u'# -*- coding: utf-8 -*-\n')
        f.write(u'"""按场景的掉落池（真值）。由 gen_drop_data.py 生成，勿手改。\n\n')
        f.write(u'来源：《梦想世界2013资料全库》场景表（场景等级 / 怪物名称 / 掉落物品）\n')
        f.write(u'      × 客户端 src/data/itemdata.py（真实物品 id、名称、【类型】/【等级】）\n')
        f.write(u'形如 {场景: {level, monsters, rare, pools=[(原始token, 解析方式, [(item_id, 物品名)])]}}\n')
        f.write(u'"""\n\n')
        f.write(u'DROP_BY_SCENE = {\n')
        for scene, d in data.items():
            f.write(u"    %r: {'level': %d,\n" % (scene, d['level']))
            f.write(u"        'monsters': %r,\n" % (d['monsters'],))
            f.write(u"        'rare': %r,\n" % (d['rare'],))
            f.write(u"        'pools': [\n")
            for token, how, hit in d['pools']:
                f.write(u"            (%r, %r, %r),\n" % (token, how, hit))
            f.write(u'        ]},\n')
        f.write(u'}\n\n')
        f.write(u'UNRESOLVED = %r\n' % (unresolved,))
        f.write(u'\n\ndef scene_pool(scene):\n')
        f.write(u'    """场景名 → 池；无该场景（城镇/未收录）返回 None。"""\n')
        f.write(u'    return DROP_BY_SCENE.get(scene)\n')

    with io.open(REPORT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(u'scene\tlevel\ttoken\tresolve\titems\tnames\n')
        for r in report:
            f.write(u'\t'.join(r) + u'\n')

    print(u'scenes: %d, pools: %d, unresolved: %d'
          % (len(data), sum(len(d['pools']) for d in data.values()), len(unresolved)))
    for scene, token in unresolved:
        print(u'  UNRESOLVED %s / %s' % (scene, token))


if __name__ == '__main__':
    main()
