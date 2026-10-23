# -*- coding: utf-8 -*-
"""把《模拟服全链路代码解析.md》拆成 docs/*.md 分册（可重复跑，幂等）。

背景（2026-10-20）：主文档 2400+ 行，单文件无限膨胀。按 docs/README.md 声明的
映射拆成分册；**主文档仍然保留**（完整版），拆分只为聚焦阅读。

切分方式：按 `## ` / `### ` 标题行切，不做任何内容改写——分册与主文档逐字一致，
避免「拆分时顺手改错」。改内容请改主文档后重跑本脚本。

产物：
  docs/README.md             5 分钟速览 + 术语表 + 分册入口表
  docs/01-architecture.md    §0-§4   启动链 / 字节层 / 登录 / 会话
  docs/02-business.md        §3,§5-§9 世界 / NPC / 战斗 / 养成 / 存档
  docs/03-observability.md   §11     tap / debugger / 覆盖
  docs/04-testing.md         §12-§13 回归 + 一致性审计
  docs/05-contracts.md       §4.2+§14 分派契约 + 硬约束清单
  docs/06-provenance.md      §10+§19 真值边界 + 合规声明
  docs/CHANGELOG-review.md   §18     复核记录
  docs/anchors/symbol-index.md  §17 符号索引（由 _gen_anchor_table 生成，勿手改）

用法：python _split_docs.py [--check]
  --check  只校验分册与主文档一致，不写盘（供回归/CI 用）
"""
from __future__ import print_function

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, '模拟服全链路代码解析.md')
DOCS = os.path.join(HERE, 'docs')
ANCHORS = os.path.join(DOCS, 'anchors')

HEADER = """# {title}

> 本册是《模拟服全链路代码解析》的拆分分册：**{desc}**。
> 完整版（唯一现状依据）：[`../模拟服全链路代码解析.md`](../模拟服全链路代码解析.md)。
> 拆分只为聚焦阅读，内容与主文档逐字一致；**改内容请改主文档**，然后重跑
> `python 协议资料/报告/_split_docs.py` 重新生成本册。
>
> 文档双向体检：`python 协议资料/报告/_check_anchors.py`（已挂进 `tests/_run_all.py` 第 ④ 层）。

---
"""


def read_main():
    return io.open(MAIN, encoding='utf-8').read().split('\n')


def find_heading(lines, prefix):
    """返回以 prefix 开头的标题行下标（0-based）。prefix 如 '## 7. ' / '### 4.2 '。"""
    hits = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit('标题 %r 命中 %d 次（期望 1 次）：%r'
                         % (prefix, len(hits), [lines[h] for h in hits]))
    return hits[0]


def slice_between(lines, start_prefix, end_prefix):
    """取 [start_prefix 标题, end_prefix 标题) 之间的全部行（含 start，不含 end）。"""
    a = find_heading(lines, start_prefix)
    b = find_heading(lines, end_prefix) if end_prefix else len(lines)
    return lines[a:b]


def section(lines, num):
    """取 '## <num>. ' 到下一个 '## ' 之间的内容。"""
    a = find_heading(lines, '## %s. ' % num)
    b = a + 1
    while b < len(lines) and not lines[b].startswith('## '):
        b += 1
    return lines[a:b]


def build():
    lines = read_main()
    out = {}

    out['README.md'] = (HEADER.format(
        title='模拟服全链路代码解析 · 分册入口',
        desc='5 分钟速览 + 术语表 + 全部分册的入口表') +
        '\n'.join(slice_between(lines, '## 阅读指引', '## 0. ')).rstrip() + '\n')

    out['01-architecture.md'] = HEADER.format(
        title='架构：启动链 / 字节层 / 登录 / 会话',
        desc='进程怎么起来、帧怎么切、握手怎么过、一条报文的主循环与分派契约') + \
        '\n'.join(section(lines, '0') + section(lines, '1') +
                  section(lines, '2') + section(lines, '3') +
                  section(lines, '4')).rstrip() + '\n'

    out['02-business.md'] = HEADER.format(
        title='业务链：世界 / NPC / 战斗 / 养成 / 存档',
        desc='从进世界到存档的完整业务生命周期，每条规则都注明真机依据') + \
        '\n'.join(section(lines, '3') + section(lines, '5') +
                  section(lines, '6') + section(lines, '7') +
                  section(lines, '8') + section(lines, '9')).rstrip() + '\n'

    out['03-observability.md'] = HEADER.format(
        title='可观测性：不改内核的旁听',
        desc='tap.py / debugger.py 如何零侵入拦截、解码、可视化全部上下行字节') + \
        '\n'.join(section(lines, '11')).rstrip() + '\n'

    out['04-testing.md'] = HEADER.format(
        title='测试与回归 + 一致性审计',
        desc='一键回归的四层防线、退出码审计、build_* ↔ OVERLAY 三角一致性') + \
        '\n'.join(section(lines, '12') + section(lines, '13')).rstrip() + '\n'

    # §4.2 是 §4 的子节，只切这一段（分派顺序契约）
    sub42 = slice_between(lines, '### 4.2 ', '### 4.3 ')
    out['05-contracts.md'] = HEADER.format(
        title='契约：分派顺序 + 全链路硬约束',
        desc='改代码前必读——顺序即契约的两种形态：_dispatch 分支序与全链路硬约束清单') + \
        '\n'.join(sub42 + section(lines, '14')).rstrip() + '\n'

    out['06-provenance.md'] = HEADER.format(
        title='真值边界与合规声明',
        desc='哪些数据有出处、哪些是本服约定，以及这个仓库能做什么、不能做什么') + \
        '\n'.join(section(lines, '10') + section(lines, '19')).rstrip() + '\n'

    out['CHANGELOG-review.md'] = HEADER.format(
        title='复核记录',
        desc='把「文档说的」和「代码现在做的」逐条对齐的过程档案') + \
        '\n'.join(section(lines, '18')).rstrip() + '\n'

    return out


def build_anchors():
    """anchors/symbol-index.md：调 _gen_anchor_table.gen() 拿当前符号表。"""
    import importlib.util
    gen_path = os.path.join(HERE, '_gen_anchor_table.py')
    spec = importlib.util.spec_from_file_location('_gen_anchor_table', gen_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = mod.gen()
    head = ('# 符号索引（脚本生成，勿手改）\n\n'
            '> 由 `python 协议资料/报告/_gen_anchor_table.py --write` 生成到主文档 §17，\n'
            '> 本文件是同一张表的分册副本，供 05-contracts / 02-business 等册内查阅。\n'
            '> 主文档 §17 与源码不一致时以主文档为准（`_check_anchors.py` 方向 B 守护）。\n\n'
            '```\n')
    return head + table + '\n```\n'


def with_index_table(text):
    """把分册入口表并入 README 正文（纯函数：返回最终内容，可反复应用）。"""
    if INDEX_BEGIN in text:                     # 已写过：整块替换
        i = text.index(INDEX_BEGIN)
        j = text.index(INDEX_END) + len(INDEX_END)
        head = text[:i].rstrip()
        while head.endswith('---'):             # 幂等修掉历史上追出来的双分隔线
            head = head[:-3].rstrip()
        return head + '\n\n' + INDEX_TABLE + text[j:]
    # 生成区自带一个 --- 结尾，追加入口表前先去掉尾部连续分隔线，避免双 ---
    body = text.rstrip()
    while body.endswith('---'):
        body = body[:-3].rstrip()
    return body + '\n\n## 分册入口表\n\n' + INDEX_TABLE + '\n'


def main(argv):
    check_only = '--check' in argv
    out = build()
    out['anchors/symbol-index.md'] = build_anchors()
    # README 的最终内容 = 生成区 + 入口表（先合成再比较，--check 才准）
    out['README.md'] = with_index_table(out['README.md'])

    if not os.path.isdir(DOCS):
        os.makedirs(DOCS)
    if not os.path.isdir(ANCHORS):
        os.makedirs(ANCHORS)

    drift = []
    for rel, text in sorted(out.items()):
        p = os.path.join(DOCS, rel.replace('/', os.sep))
        old = None
        if os.path.isfile(p):
            old = io.open(p, encoding='utf-8').read()
        if old == text:
            print('  = %s（无变化）' % rel)
            continue
        drift.append(rel)
        if check_only:
            print('  ✗ %s 与主文档不一致（--check 模式不写盘）' % rel)
            continue
        with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print('  %s %s（%d 行）' % ('=' if old is None else '~', rel,
                                   text.count('\n')))

    if check_only and drift:
        print('分册体检：%d 个文件与主文档不一致' % len(drift))
        return 1
    print('分册生成完成：%d 个文件' % len(out))
    return 0


INDEX_BEGIN = '<!-- BEGIN: 分册入口表（_split_docs.py 维护） -->'
INDEX_END = '<!-- END: 分册入口表 -->'
INDEX_TABLE = INDEX_BEGIN + """
| 文件 | 内容 | 对应主文档章节 |
|---|---|---|
| `README.md` | 5 分钟速览 + 术语表 + 入口表 | §阅读指引 + §术语表 |
| `01-architecture.md` | 启动链 + 字节层 + 登录 + 会话主循环 | §0-§4 |
| `02-business.md` | 世界 / NPC / 战斗 / 养成 / 存档 | §3, §5-§9 |
| `03-observability.md` | tap / debugger / 覆盖表 | §11 |
| `04-testing.md` | 一键回归 + 退出码审计 + 一致性审计 | §12-§13 |
| `05-contracts.md` | 分派顺序契约 + 全链路硬约束清单 | §4.2 + §14 |
| `06-provenance.md` | 真值 / 模拟参数分界 + 合规声明 | §10 + §19 |
| `CHANGELOG-review.md` | 复核记录 | §18 |
| `anchors/symbol-index.md` | 符号索引（脚本生成副本） | §17 |

**只在主文档的分册外章节**：§15 边界与已知风险（含 15.0 变异测试 / 15.1 特性状态表 /
15.2 Web 端口顺延）、§16 扩展指南、§17 符号索引原文。查这四块请开主文档。
""" + INDEX_END


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
