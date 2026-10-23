# -*- coding: utf-8 -*-
"""L{} 循环构造的结构化解码（op105/147/148/149）。

背景：这些帧的列表字段是「循环到哨兵」或「循环 N 次」，
原先用 T（吃剩余字节）只给 hex，看不出有几条、每条什么值。

本测试锁定：
  1. 四个 op 都能结构化解出条目（fit=True，tail=0）
  2. 条目字段值正确（不是解出个空列表就算过）
  3. 汇总行在明细行之前（阅读顺序）
  4. 哨兵终止与计数终止两种控制都工作
  5. 畸形输入不崩（哨兵缺失、计数超界）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import tap as T  # noqa: E402
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def decode(op, frame):
    parts, _ = T.split_frames('S2C', frame)
    body = parts[0][2]
    p = T.Protocol()
    return T.decode_best(p.describe('S2C', op, body)['layouts'], body)


def main():
    # ── 1. 哨兵终止：op147 / op148 / op149 ──
    cases = [
        (147, m.build_npc_buylist(7, [(101, 5, 20)]), 'items',
         {'shape': 101, 'num': 5, 'price': 20}),
        (148, m.build_npc_sell_list([(101, 5, 50)]), 'items',
         {'shape': 101, 'amount': 5, 'price': 50}),
        (149, m.build_npc_learn_list([(1104, 0, 255, 0)]), 'entries',
         {'skid': 1104, 'npcLimit': 0, 'validLearn': 255, 'freeLevel': 0}),
    ]
    for op, frame, field, want in cases:
        dec = decode(op, frame)
        check('op%d 解出且吃满' % op, dec['fit'] and dec['tail_len'] == 0,
              'fit=%r tail=%d' % (dec['fit'], dec['tail_len']))
        got = next((r['value'] for r in dec['rows']
                    if r['name'] == field and isinstance(r.get('value'), list)),
                   None)
        check('op%d %s 条目内容正确' % (op, field), got == [want],
              'got=%r' % (got,))

    # ── 2. 计数终止：op105 ──
    dec = decode(105, m.build_map_summon(1, [(0, 4005, '梦想企鹅', '')]))
    check('op105 解出且吃满', dec['fit'] and dec['tail_len'] == 0,
          'fit=%r tail=%d' % (dec['fit'], dec['tail_len']))
    ents = next((r['value'] for r in dec['rows']
                 if r['name'] == 'entries' and isinstance(r.get('value'), list)),
                None)
    check('op105 entries 字段完整（style/shape/desc/name/title）',
          ents == [{'style': 0, 'shape': 4005, 'desc': None,
                    'name': '梦想企鹅', 'title': ''}],
          'got=%r' % (ents,))

    # ── 2b. op105 边界：多宠物 / 带称号 / 空列表 / 中文名 ──
    #   op105 是**计数终止**（count:n）而非哨兵终止，且循环体含 D/V 类型，
    #   是四个 op 里最复杂的一个，边界必须单独覆盖。
    def _105(entries):
        return decode(105, m.build_map_summon(1, entries))

    def _ents(dec):
        return next((r['value'] for r in dec['rows']
                     if r['name'] == 'entries' and isinstance(r.get('value'), list)),
                    None)

    d = _105([(0, 4005, '企鹅A', ''), (1, 4006, '企鹅B', '称号X')])
    check('op105 多宠物（n=2）', d['fit'] and len(_ents(d) or []) == 2,
          'got=%r' % (_ents(d),))
    check('op105 多宠物条目内容正确',
          _ents(d) == [{'style': 0, 'shape': 4005, 'desc': None,
                        'name': '企鹅A', 'title': ''},
                       {'style': 1, 'shape': 4006, 'desc': None,
                        'name': '企鹅B', 'title': '称号X'}],
          'got=%r' % (_ents(d),))

    d = _105([(0, 4005, '梦想企鹅', '变异')])
    check('op105 带称号', _ents(d) == [{'style': 0, 'shape': 4005, 'desc': None,
                                        'name': '梦想企鹅', 'title': '变异'}],
          'got=%r' % (_ents(d),))

    d = _105([])
    check('op105 空列表（n=0）解为 []', d['fit'] and _ents(d) == [],
          'fit=%r got=%r' % (d['fit'], _ents(d)))

    d = _105([(0, 4005, '一二三四五六七八', '')])
    check('op105 中文名（多字节长度前缀）',
          _ents(d) == [{'style': 0, 'shape': 4005, 'desc': None,
                        'name': '一二三四五六七八', 'title': ''}],
          'got=%r' % (_ents(d),))

    # ── 3. 汇总行在明细行之前 ──
    names = [r['name'] for r in dec['rows']]
    if 'entries' in names and 'entries[0]' in names:
        check('汇总行排在明细行之前',
              names.index('entries') < names.index('entries[0]'),
              'names=%r' % (names,))
    else:
        check('汇总行与明细行都存在', False, 'names=%r' % (names,))

    # ── 4. 多条目 ──
    multi = m.build_npc_buylist(9, [(101, 5, 20), (102, 3, 40), (103, 1, 99)])
    dec = decode(147, multi)
    items = next((r['value'] for r in dec['rows']
                  if r['name'] == 'items' and isinstance(r.get('value'), list)),
                 None)
    check('多条目全部解出（3 条）', items is not None and len(items) == 3,
          'got=%r' % (items,))
    check('多条目值正确',
          items == [{'shape': 101, 'num': 5, 'price': 20},
                    {'shape': 102, 'num': 3, 'price': 40},
                    {'shape': 103, 'num': 1, 'price': 99}],
          'got=%r' % (items,))

    # ── 5. 空列表（只有哨兵）──
    empty = m.build_npc_buylist(7, [])
    dec = decode(147, empty)
    items = next((r['value'] for r in dec['rows']
                  if r['name'] == 'items' and isinstance(r.get('value'), list)),
                 None)
    check('空列表解为 []（只有哨兵）', items == [], 'got=%r' % (items,))

    # ── 6. 畸形输入不崩 ──
    try:
        bad = decode(147, T.make_s2c(147, b'\x02' + b'\x00' * 3))
        check('截断的 op147 不崩（解不出就如实报）', True,
              'fit=%r note=%r' % (bad['fit'], (bad['note'] or '')[:40]))
    except Exception as e:
        check('截断的 op147 不崩', False, repr(e))

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('L{} 循环解码：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
