# -*- coding: utf-8 -*-
"""按名字查 itemdata 的物品 id（复用 gen_drop_data.parse_items 的正则口径）。

用法：
    python _find_item.py 刃虎宝宝 丝绸手绢 ...     # 精确名
    python _find_item.py -f 宝石包                # 模糊
"""
import io
import os
import sys

MX = r'C:\Users\Administrator\MX'
sys.path.insert(0, os.path.join(MX, '服务端源码', 'tests', 'sim'))
from gen_drop_data import parse_items          # noqa: E402

ITEMS = parse_items()
BY_NAME = {}
for iid, (nm, desc) in ITEMS.items():
    BY_NAME.setdefault(nm, []).append(iid)


def exact(name):
    return sorted(BY_NAME.get(name) or [])


def fuzzy(sub):
    return sorted((iid, nm) for iid, (nm, _d) in ITEMS.items() if sub in nm)


def main(argv):
    if not argv:
        print('items', len(ITEMS))
        return 0
    if argv[0] == '-f':
        for sub in argv[1:]:
            hit = fuzzy(sub)
            print('~%s (%d): %s' % (sub, len(hit),
                                    ', '.join('%d:%s' % h for h in hit[:14])))
        return 0
    for name in argv:
        ids = exact(name)
        print('%-10s %s' % (name, ('%s' % ','.join(map(str, ids)) if ids
                                   else '—— 查无')))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
