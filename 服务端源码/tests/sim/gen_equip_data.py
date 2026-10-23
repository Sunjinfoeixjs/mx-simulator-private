# -*- coding: utf-8 -*-
u"""从客户端 `data/itemdata.py` 生成 `equip_data.py`（装备真值表）。

为什么生成而不是手写
--------------------
装备「能不能穿、穿哪个槽、要几级」三条都是客户端真值，但分散在三处：

  · `ITEMTYPE`（itemdata.py）= 类型 → **id 区间**（如 cap 60000-60999）
  · `EQUIP2POS`（itemdata.py）= 类型 → **装备位**（cap→1, weapon→2 …）
  · 每条物品的 `desc` 尾部 = `【等级】30` / `【装备要求】50级剑法技巧`

252 条装备手抄必错，且客户端一升级就过期。所以本脚本只做**提取**，
产物 `equip_data.py` 带着「由谁生成、从哪来」的头，任何人可重跑核对。

真值 / 缺口
-----------
  OK 槽位：`EQUIP2POS` 直读，无推断。
  OK 等级：`【等级】N` 直读（128 件有）；没有该字段的装备**不给等级门**
     ——它的门槛写在 `【装备要求】` 里（如「50级剑法技巧」），那是**技能**等级
     不是人物等级，本服没有剑法技巧体系（见 equipment_rules 的 SKILL_GAP）。
  XX 攻防数值：itemdata **没有**任何攻/防/耐久字段，只有一段文学描述。
     故本表**不含**任何数值，装备加成一律不可得（硬性约束：不猜属性真值）。

用法：
    python 服务端源码/tests/sim/gen_equip_data.py            # 重写 equip_data.py
    python 服务端源码/tests/sim/gen_equip_data.py --check    # 只校验是否过期
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))   # sim → tests → 服务端源码 → MX
ITEMDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'itemdata.py')
OUT = os.path.join(HERE, 'equip_data.py')

# ★ 与客户端 itemdata.EQUIP2POS 逐字一致（顺序不影响结果：各类区间互不相交）
EQUIP2POS = {'cap': 1, 'weapon': 2, 'cloth': 3, 'sash': 4, 'cuff': 5,
             'magic': 6, 'necklace': 7, 'ring': 8, 'shoes': 9}
# ★ 与客户端 itemdata.ITEMTYPE 的区间一致（仅取装备相关项）
TYPE_RANGES = {
    'weapon': ((50000, 59999), (68001, 68041), (68501, 68505), (14208, 14250)),
    'cap': ((60000, 60999),),
    'cloth': ((61000, 61999),),
    'shoes': ((62000, 62999),),
    'cuff': ((63000, 63999),),
    'necklace': ((64000, 64999),),
    'ring': ((65000, 65999),),
    'sash': ((66000, 66999),),
    'magic': ((67000, 67999),),
}
# ITEMTYPE['weapon'] 里两个**单点** id（不是区间），客户端源码就是裸数字
TYPE_SINGLES = {'weapon': (68513, 68361)}
# 槽位顺序表：产物按槽位排序，便于人工核对
SLOT_NAMES = {1: '头盔', 2: '武器', 3: '衣服', 4: '腰带', 5: '护腕',
              6: '法宝', 7: '项链', 8: '戒指', 9: '鞋子'}


def load_itemdata():
    """→ (items, type_of, slot_of)

    直接 exec 客户端 itemdata.py 不可行（Python 2 语法），故只做**字面量提取**：
    `ITEMTYPE` 用括号配对取出后 `ast.literal_eval`，物品行用与
    `gen_drop_data.parse_items` 完全相同的正则（口径统一，避免两处解析漂移）。
    """
    sys.path.insert(0, HERE)
    from gen_drop_data import parse_items        # 复用它，不重写一遍正则
    items = parse_items()
    return items


def _grab_literal(src, name):
    """取 `name = {...}` 的平衡括号字面量文本。"""
    i = src.find(name + ' = ')
    if i < 0:
        raise AssertionError('客户端 itemdata 里找不到 %s' % name)
    k = src.find('{', i)
    depth = 0
    for j in range(k, len(src)):
        c = src[j]
        if c in '{([':
            depth += 1
        elif c in '})]':
            depth -= 1
            if depth == 0:
                return src[k:j + 1]
    raise AssertionError('%s 括号不配平' % name)


def crosscheck_client_tables():
    """把脚本内的 EQUIP2POS / 区间与客户端源码**逐项对账**，不一致直接抛。

    ★ 这一步是「不手抄真值」的保险：数据搬家最容易错，且错了不会报错，
      只会在几个月后表现为「某件装备穿不上」。宁可 import 期炸掉。
    """
    import ast
    src = io.open(ITEMDATA, encoding='gbk', errors='replace').read()
    c_e2p = ast.literal_eval(_grab_literal(src, 'EQUIP2POS'))
    if c_e2p != EQUIP2POS:
        raise AssertionError('EQUIP2POS 与客户端不一致：\n  本脚本 %s\n  客户端 %s'
                             % (EQUIP2POS, c_e2p))
    c_itemtype = ast.literal_eval(_grab_literal(src, 'ITEMTYPE'))
    for k, ranges in TYPE_RANGES.items():
        # 客户端把「单点 id」写成裸数字（如 weapon 的 68513/68361），本脚本单独放在
        # TYPE_SINGLES；两边都先归一成 (lo, hi) 再比，否则永远差两项。
        want = tuple(r if isinstance(r, tuple) else (r, r)
                     for r in (c_itemtype.get(k) or ()))
        got = tuple(r if isinstance(r, tuple) else (r, r) for r in ranges) \
            + tuple((n, n) for n in TYPE_SINGLES.get(k, ()))
        if got != want:
            raise AssertionError('ITEMTYPE[%r] 与客户端不一致：\n  本脚本 %s\n  客户端 %s'
                                 % (k, got, want))


RE_LEVEL = re.compile(u'【等级】\\s*(\\d+)')
RE_REQUIRE = re.compile(u'【装备要求】\\s*([^#\\r\\n]{1,24})')
RE_KIND = re.compile(u'【类型】\\s*([^#\\r\\n]{1,16})')


def kind_of(shape):
    """id → 客户端类型名（cap/weapon/…）；非装备返回 None。"""
    for k, ranges in TYPE_RANGES.items():
        for lo, hi in ranges:
            if lo <= shape <= hi:
                return k
    for k, singles in TYPE_SINGLES.items():
        if shape in singles:
            return k
    return None


def build():
    items = load_itemdata()
    rows = {}
    stat = {'equip': 0, 'with_level': 0, 'with_require': 0, 'no_level': 0}
    for shape, (name, desc) in items.items():
        k = kind_of(shape)
        if k is None:
            continue
        stat['equip'] += 1
        slot = EQUIP2POS[k]
        m = RE_LEVEL.search(desc)
        level = int(m.group(1)) if m else None
        if level is None:
            stat['no_level'] += 1
        else:
            stat['with_level'] += 1
        m = RE_REQUIRE.search(desc)
        require = m.group(1).strip() if m else ''
        if require:
            stat['with_require'] += 1
        m = RE_KIND.search(desc)
        kindtext = m.group(1).strip() if m else ''
        rows[shape] = (name, k, slot, level, require, kindtext)
    return rows, stat


def render(rows, stat):
    lines = []
    lines.append(u'# -*- coding: utf-8 -*-')
    lines.append(u'"""装备真值表（**生成物，勿手改**）。')
    lines.append(u'')
    lines.append(u'由 `gen_equip_data.py` 从客户端 `data/itemdata.py` 提取：')
    lines.append(u'  · 类型/槽位 ← `ITEMTYPE` + `EQUIP2POS`（客户端代码直读）')
    lines.append(u'  · 等级     ← desc 的 `【等级】N`（没有该字段就不设等级门）')
    lines.append(u'  · 装备要求 ← desc 的 `【装备要求】…`（多为「50级剑法技巧」，本服无此体系）')
    lines.append(u'')
    lines.append(u'⚠ 本表**不含任何攻/防/耐久数值**：客户端 itemdata 只有文学描述，')
    lines.append(u'  没有装备属性字段。装备加成属证据缺口，见 equipment_rules.STAT_GAP。')
    lines.append(u'')
    lines.append(u'共 %d 条：有等级门 %d / 无等级门 %d / 带装备要求 %d'
                 % (stat['equip'], stat['with_level'], stat['no_level'],
                    stat['with_require']))
    lines.append(u'"""')
    lines.append(u'')
    lines.append(u'# shape: (名称, 客户端类型, 槽位, 等级门槛 or None, 装备要求原文, 类型原文)')
    lines.append(u'EQUIP_INFO = {')
    for shape in sorted(rows):
        name, k, slot, level, require, kindtext = rows[shape]
        lines.append(u'    %d: (%r, %r, %d, %r, %r, %r),'
                     % (shape, name, k, slot, level, require, kindtext))
    lines.append(u'}')
    lines.append(u'')
    lines.append(u'# 槽位 → 中文名（1 头 2 武 3 衣 4 腰 5 腕 6 法宝 7 项链 8 戒指 9 鞋）')
    lines.append(u'SLOT_NAMES = {')
    for slot in sorted(SLOT_NAMES):
        lines.append(u'    %d: %r,' % (slot, SLOT_NAMES[slot]))
    lines.append(u'}')
    lines.append(u'')
    lines.append(u'# 类型 → 槽位（客户端 EQUIP2POS 原样）')
    lines.append(u'EQUIP2POS = {')
    for k in sorted(EQUIP2POS):
        lines.append(u'    %r: %d,' % (k, EQUIP2POS[k]))
    lines.append(u'}')
    lines.append(u'')
    return u'\n'.join(lines)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只校验产物是否过期')
    args = ap.parse_args(argv)

    crosscheck_client_tables()
    rows, stat = build()
    text = render(rows, stat)
    old = io.open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else u''
    if args.check:
        if old != text:
            print(u'equip_data.py 已过期，请重跑 gen_equip_data.py')
            return 1
        print(u'equip_data.py 与客户端一致（%d 条装备）' % len(rows))
        return 0
    io.open(OUT, 'w', encoding='utf-8', newline='\n').write(text)
    print(u'已写出 %s' % OUT)
    print(u'  装备 %d 条：有等级门 %d / 无等级门 %d / 带装备要求 %d'
          % (stat['equip'], stat['with_level'], stat['no_level'],
             stat['with_require']))
    by_slot = {}
    for (name, k, slot, lv, rq, kt) in rows.values():
        by_slot[slot] = by_slot.get(slot, 0) + 1
    print(u'  按槽位：%s' % '、'.join(
        u'%s %d' % (SLOT_NAMES[s], by_slot[s]) for s in sorted(by_slot)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
