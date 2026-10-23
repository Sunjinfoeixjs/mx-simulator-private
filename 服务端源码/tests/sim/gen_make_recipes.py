# -*- coding: utf-8 -*-
"""生成制作配方表 `make_recipes.py`（产物真、材料真、配比模拟）。

数据来源
--------
· 产物目录（真值）：客户端 `src/data/craftdata.py` 的 `DATA`
  —— 48 条，id 25201-25248，lv 5-120，cost 16-58。
· 产物名称（真值）：客户端 `src/data/itemdata.py` 的 `DATA[shape][0]`（交叉校验）。
· 材料（真值 id）：`itemdata` 里**真实存在**的材料物品（纸张/草叶/松木/布料/丝绸/
  矿石/琉璃/水晶/血藤…），id 全部经过 itemdata 校验，不是编的。
· 材料配比（**模拟参数**）：客户端与《资料全库》都只有「产物名称/等级/介绍」，
  **没有材料→产物表**（原版配方在服务端）。这里按产物名称里的材质词归类，
  件数 = 2 + lv//20（2..8），同材质同件数撞车时 +1，保证 key 唯一。

归类规则（按顺序首次命中）
------------------------
剪纸/年画/脸谱→纸；草编/竹编→草叶；刺绣→布料；丝绸→丝绸；葫芦/檀香/根雕/沉香/木→松木；
陶瓷/瓷/紫砂→红矿石；古藤→血藤；琉璃→绿琉璃；水晶→红水晶；
翡翠/珊瑚/玉/角/夜光→玉矿石；青铜/银→银矿石；鎏金/金→金矿石；
未命中按等级兜底（≥100 金矿石，≥80 银矿石，≥60 玉矿石，≥40 丝绸，其余松木）。

用法： python gen_make_recipes.py
"""
from __future__ import print_function

import ast
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(HERE))          # MX 根
DATA_SRC = os.path.join(MX, '客户端源码', 'src', 'data')
OUT = os.path.join(HERE, 'make_recipes.py')

# 真实材料：shape -> (名称, 模拟售价)。id 全部来自 itemdata，名称在生成时复核。
MATERIALS = {
    10155: ('纸张', 40),
    10380: ('鲜嫩草叶', 30),
    10158: ('布料', 60),
    10429: ('松木', 80),
    10296: ('红矿石', 150),
    14203: ('血藤', 300),
    22211: ('玉矿石', 600),
    22209: ('银矿石', 300),
    22210: ('金矿石', 500),
    22309: ('丝绸', 400),
    14331: ('绿琉璃', 800),
    14266: ('红水晶', 900),
}

# 名称关键词 -> 材料 shape（顺序敏感：鎏金 必须在 金 之前，珊瑚 在 玉 之前）
RULES = (
    ('鎏金', 22210),
    ('剪纸', 10155), ('年画', 10155), ('脸谱', 10155),
    ('草编', 10380), ('竹编', 10380),
    ('刺绣', 10158),
    ('丝绸', 22309),
    ('葫芦', 10429), ('檀香', 10429), ('根雕', 10429), ('沉香', 10429),
    ('陶瓷', 10296), ('瓷', 10296), ('紫砂', 10296),
    ('古藤', 14203),
    ('琉璃', 14331),
    ('水晶', 14266),
    ('翡翠', 22211), ('珊瑚', 22211), ('玉', 22211), ('角', 22211), ('夜光', 22211),
    ('青铜', 22209), ('银', 22209),
    ('金', 22210),
    ('木', 10429),
)


def load_dict(path, name='DATA'):
    """抽取模块级大字典字面量（反编译产物有非法语法，不能直接 import）。"""
    src = io.open(path, encoding='utf-8', errors='replace').read()
    start = src.index('%s = {' % name) + len('%s = ' % name)
    depth, i, quote, esc = 0, start, None, False
    while i < len(src):
        ch = src[i]
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
                    i += 1
                    break
        i += 1
    return ast.literal_eval(src[start:i])


def gbk(v):
    return v.decode('gbk', 'replace') if isinstance(v, bytes) else str(v)


def pick_material(name, lv):
    for kw, shape in RULES:
        if kw in name:
            return shape, kw
    if lv >= 100:
        return 22210, '兜底'
    if lv >= 80:
        return 22209, '兜底'
    if lv >= 60:
        return 22211, '兜底'
    if lv >= 40:
        return 22309, '兜底'
    return 10429, '兜底'


def main():
    craft = load_dict(os.path.join(DATA_SRC, 'craftdata.py'))
    items = load_dict(os.path.join(DATA_SRC, 'itemdata.py'))

    # 材料 id / 名称必须先核实：不存在的 id 直接让生成失败，别写出假表
    for shape, (want, _price) in MATERIALS.items():
        if shape not in items:
            raise SystemExit('材料 shape %d 不存在于 itemdata' % shape)
        real = gbk(items[shape][0])
        if real != want:
            raise SystemExit('材料 %d 名称不符：生成器写 %r，itemdata 是 %r'
                             % (shape, want, real))

    order = sorted(craft)
    seen = set()
    recipes, used, warns = [], set(), []
    for pid in order:
        v = craft[pid]
        name = gbk(items.get(pid, (b'', b''))[0]) if pid in items else gbk(v['name'])
        if pid in items and gbk(items[pid][0]) != gbk(v['name']):
            warns.append('%d craftdata=%s itemdata=%s'
                         % (pid, gbk(v['name']), gbk(items[pid][0])))
        shape, how = pick_material(name, int(v['lv']))
        count = 2 + int(v['lv']) // 20
        while (shape, count) in seen:          # 同材质同件数撞车 → +1，保证 key 唯一
            count += 1
        seen.add((shape, count))
        used.add(shape)
        recipes.append((pid, shape, count, how))

    lines = []
    add = lines.append
    add('# -*- coding: utf-8 -*-')
    add('"""制作配方表（生成物，勿手改）。')
    add('')
    add('产物目录 = 真值：客户端 src/data/craftdata.py（48 条，25201-25248，lv 5-120）')
    add('              × src/data/itemdata.py（真实物品名，交叉校验）。')
    add('材料     = 真值 id：itemdata 里真实存在的材料物品（纸张/草叶/松木/丝绸/矿石…）。')
    add('材料配比 = **模拟参数**：两张客户端表 + 《资料全库》都没有「材料→产物」对应，')
    add('           原版配方在服务端。规则：按产物名里的材质词归类，件数 = 2 + lv//20，')
    add('           撞车则 +1 保证 key 唯一（详见 gen_make_recipes.py）。')
    add('')
    add('再生成： python gen_make_recipes.py')
    add('"""')
    add('')
    add('# 材料在售清单（shape, 名称, 模拟售价）：接进 SHOP_ITEMS，保证买得到才谈得上制作')
    add('MATERIAL_SHOP = (')
    for shape in sorted(used, key=lambda s: (MATERIALS[s][1], s)):
        nm, price = MATERIALS[shape]
        add("    (%d, '%s', %d)," % (shape, nm, price))
    add(')')
    add('')
    add('# 产物目录：id -> {name, lv, cost, desc}')
    add('CRAFT_DATA = {')
    for pid in order:
        v = craft[pid]
        name = gbk(items.get(pid, (b'', b''))[0]) if pid in items else gbk(v['name'])
        add("    %d: {'name': '%s', 'lv': %d, 'cost': %d, 'desc': '%s'},"
            % (pid, name, int(v['lv']), int(v['cost']),
               gbk(v.get('desc', b'')).replace("'", "\\'")))
    add('}')
    add('')
    add('# 材料组合 -> 产物 id；组合 = ((shape, 件数), ...) 升序')
    add('RECIPES = {')
    for pid, shape, count, _how in recipes:
        nm = MATERIALS[shape][0]
        add('    ((%d, %d),): %d,   # %s ×%d → %s(lv%d)'
            % (shape, count, pid, nm, count,
               gbk(items.get(pid, (b'', b''))[0]) if pid in items else '', craft[pid]['lv']))
    add('}')
    add('')

    io.open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
    print('已生成 %s：产物 %d 条，配方 %d 条，材料 %d 种'
          % (OUT, len(order), len(recipes), len(used)))
    print('材料：' + '、'.join('%s(%d)' % (MATERIALS[s][0], s) for s in sorted(used)))
    fb = [r for r in recipes if r[3] == '兜底']
    if fb:
        print('兜底归类 %d 条：%s' % (len(fb), [r[0] for r in fb]))
    if warns:
        print('⚠ 名称不一致 %d 条（已按 itemdata）：' % len(warns))
        for w in warns[:8]:
            print('   ' + w)
    assert len({(r[1], r[2]) for r in recipes}) == len(recipes), '配方 key 撞车'
    print('key 唯一性自检通过')


if __name__ == '__main__':
    main()
