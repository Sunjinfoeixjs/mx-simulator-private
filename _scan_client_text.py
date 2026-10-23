# -*- coding: utf-8 -*-
r"""在客户端反编译源码里按**中文**搜字符串。

为什么需要它：客户端 `.py` 里的中文是 GBK **转义字节串**（如
`b'\xc7\xbf\xbb\xaf'`），直接 grep 中文一个都搜不到，grep 文件本身
（按 gbk 解码）也搜不到——字节串在源码里是 ASCII 转义形式。

做法：把每个 `b'...'` 字面量还原成 bytes → 按 gbk 解码 → 再查关键词，
顺带给出所在文件与行号。

用法：
    python _scan_client_text.py 强化 修理 耐久
    python _scan_client_text.py -f 强化      # 只列文件与条数
"""
import codecs
import io
import os
import re
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '客户端源码', 'src')
LIT = re.compile(r"b'((?:[^'\\]|\\.)*)'")


def decode_lit(body):
    """把字面量正文（latin-1 视角）还原成 bytes 再 gbk 解码；失败返回 ''。"""
    try:
        raw = codecs.escape_decode(body.encode('latin-1'))[0]
    except Exception:
        return ''
    try:
        return raw.decode('gbk')
    except Exception:
        return ''


def scan(keywords):
    """→ {path: [(lineno, kw, text), ...]}"""
    out = {}
    for root, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(root, fn)
            try:
                src = io.open(path, encoding='latin-1').read()
            except Exception:
                continue
            rows = []
            for m in LIT.finditer(src):
                txt = decode_lit(m.group(1))
                if not txt:
                    continue
                for kw in keywords:
                    if kw in txt:
                        rows.append((src.count('\n', 0, m.start()) + 1, kw, txt))
                        break
            if rows:
                out[os.path.relpath(path, SRC)] = rows
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    only_files = argv[0] == '-f'
    kws = argv[1:] if only_files else argv
    hits = scan(kws)
    total = sum(len(v) for v in hits.values())
    print('关键词：%s  命中 %d 处 / %d 个文件'
          % ('、'.join(kws), total, len(hits)))
    for path in sorted(hits):
        rows = hits[path]
        if only_files:
            print('  %-44s %d' % (path, len(rows)))
            continue
        print('\n── %s（%d）──' % (path, len(rows)))
        for line, kw, txt in rows[:12]:
            print('  L%-6d %s' % (line, txt.replace('\n', ' ')[:96]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
