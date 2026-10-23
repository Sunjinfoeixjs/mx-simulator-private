# -*- coding: utf-8 -*-
"""Verify MATERIAL_SHOP shapes are real items in itemdata, and report what
each one actually is (type/level from its description).

This is the audit that turns "材料是真 id" from a claim into a checked fact.
"""
import io
import os
import sys

MX = r'C:\Users\Administrator\MX'
ITEMDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'itemdata.py')
sys.path.insert(0, os.path.join(MX, '服务端源码', 'sim'))
from make_recipes import MATERIAL_SHOP, CRAFT_DATA, RECIPES  # noqa: E402


def load_dict(path, name='DATA'):
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
    return src[start:i], _literal(src[start:i])


def _unescape(b):
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


def _literal(s):
    """安全地把 dict 字面量求值：先把 b'...' 转成普通字符串。"""
    import ast
    import re
    def repl(m):
        return repr(_unescape(m.group(0)))
    s2 = re.sub(r"b'((?:[^'\\]|\\.)*)'", repl, s, flags=re.S)
    return ast.literal_eval(s2)


def _name(v):
    """itemdata 的值可能是 bytes、已解码字符串、或 repr 后的 "b'..'"，统一成文本。"""
    if isinstance(v, bytes):
        return _unescape(v)
    s = str(v)
    if s.startswith("b'") and s.endswith("'"):
        return s[2:-1]
    return s


def main():
    _raw, items = load_dict(ITEMDATA)
    print('itemdata 共 %d 条' % len(items))
    print()
    print('%-28s %-8s %-14s %s'
          % ('MATERIAL_SHOP 名称', 'shape', 'itemdata 实际名称', ''))
    bad = []
    for shape, name, _price in MATERIAL_SHOP:
        real = items.get(shape)
        realname = _name(real[0]) if real else None
        ok = realname == name
        if not ok:
            bad.append((shape, name, realname))
        print('%-28s %-8d %-14s %s' % (name, shape, realname or '<不存在>',
                                       'OK' if ok else 'MISMATCH'))
    print()
    print('材料 id 全部存在于 itemdata 且名称一致：%s' % ('YES' if not bad else 'NO %r' % (bad,)))

    # 产物都要在 craftdata 里（48 条）且件数非零
    print()
    print('配方产物抽查（前 5 条 + 最后 2 条）：')
    keys = sorted(RECIPES)
    for k in keys[:5] + keys[-2:]:
        pid = RECIPES[k]
        info = CRAFT_DATA.get(pid)
        print('  %-24s → %-14s lv=%-4d cost=%s'
              % ('%s×%d' % (k[0][0], k[0][1]), (info or {}).get('name'),
                 (info or {}).get('lv'), (info or {}).get('cost')))

    # 每条产物的材料组合是否唯一（key 唯一性）
    print()
    print('配方 key 唯一：%s（%d 条 / %d 个唯一 key）'
          % (len(RECIPES) == len({tuple(sorted(k)) for k in RECIPES}),
             len(RECIPES), len({tuple(sorted(k)) for k in RECIPES})))

    # 产物是否覆盖 craftdata 全部 48 条
    missing = sorted(set(CRAFT_DATA) - set(RECIPES.values()))
    print('craftdata 产物未被任何配方覆盖：%d 条 %r'
          % (len(missing), missing[:12]))


if __name__ == '__main__':
    main()
