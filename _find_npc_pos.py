# -*- coding: utf-8 -*-
"""解码 npcposdata.DATA，按名字查 NPC 的 (mapid, x, y)。"""
import io
import re
import sys

SRC = r'C:\Users\Administrator\MX\客户端源码\src\data\npcposdata.py'


def load():
    s = io.open(SRC, encoding='utf-8', errors='replace').read()
    # 去掉反编译产物里的模块级裸 return（在函数外会 SyntaxError）
    lines = []
    depth = 0
    for ln in s.splitlines():
        st = ln.strip()
        if st.startswith('def '):
            depth += 1 if depth == 0 else 1
        lines.append(ln)
    # 简单法：exec 前把「顶格 return」删掉
    s2 = '\n'.join(('' if ln.strip() == 'return' and not ln.startswith(' ')
                    else ln) for ln in s.splitlines())
    ns = {}
    exec(compile(s2, SRC, 'exec'), ns)
    return ns['DATA']


def main():
    data = load()
    want = ['金大有', '朱能', '赵捕头', '龙蛇老人', '巫道人', '狼武使者',
            '战锋·星月', '吴教头', '烈阳锋·血牙', '薇薇安·赤瞳', '天乾',
            '蒙面怪客', '神羿', '加洛国王', '清岚国王', '南遥国王', '天宁国王',
            '孙秀才', '锦毛鼠', '花王李', '铃铛猫女', '海外商人', '船夫',
            '马群', '兔保镖', '兔工匠', '追债打手', '唐师傅', '跑堂小二',
            '巧手张', '李大娘', '钱捕头', '冯大叔', '江湖一阵风', '狼钻风']
    hits = {w: [] for w in want}
    total = 0
    for mapid, rows in data.items():
        for row in rows:
            total += 1
            try:
                name = row[0].decode('gbk', 'replace') if isinstance(row[0], bytes) else str(row[0])
            except Exception:
                continue
            pos = row[4] if len(row) > 4 else None
            if name in hits:
                hits[name].append((mapid, pos))
    print('npc rows', total)
    for w in want:
        print('%-8s %s' % (w, hits[w][:4] if hits[w] else '—— 无'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
