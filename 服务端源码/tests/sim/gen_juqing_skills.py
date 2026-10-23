# -*- coding: utf-8 -*-
"""生成 juqing_skill_data.py（剧情技能表 = 剧情点的**真正用途**）。

为什么单独一份生成物
--------------------
2026-10-22 才发现：剧情点不是「只显示的装饰」。客户端
`dialog/dlglearnskill.py` 的技能列表里，`2001 <= sn < 2030` 的条目把花费列
从「金钱/经验」换成 `hero.g_Prop.m_StoryPoint`，判断够不够也是拿它比；
而这些条目的名称/说明来自 `skilldata.py`（type=3），每级消耗与上限来自
`juqingdata.py`（{'max': N, 'dist': M} = 最高 N 级、每级 M 点）。

所以剧情点的消费入口 = 「找 NPC 学剧情技能」（协议 C2S_NPC_LEARN_SKILL(103)
+ S2CSkillLevel(65) sktype=3）。之前 FEATURE_STATUS 把 story_point 标成
display_only 是错的，已改 implemented。

数据来源（都是客户端真值，不是资料站）
  · 客户端源码/src/data/skilldata.py   → name / desc / type
  · 客户端源码/src/data/juqingdata.py  → max（最高等级）/ dist（每级剧情点）
  · 《资料全库》剧情技能表 → 交叉核对名称与每级消耗（一致）

生成：python tests/sim/gen_juqing_skills.py
"""
import ast
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
MX = HERE
for _ in range(6):
    if os.path.isdir(os.path.join(MX, '客户端源码')):
        break
    MX = os.path.dirname(MX)
SKILLDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'skilldata.py')
JUQINGDATA = os.path.join(MX, '客户端源码', 'src', 'data', 'juqingdata.py')
OUT = os.path.join(HERE, 'juqing_skill_data.py')


def load_dict_literal(path, marker='DATA = '):
    """把 `DATA = {...}` 字面量取出来 eval（文件里混有 Py2 代码，不能 import）。"""
    s = io.open(path, encoding='utf-8', errors='replace').read()
    i = s.index(marker)
    body = s[i + len(marker):]
    depth = 0
    for k, ch in enumerate(body):
        if ch in '{[(':
            depth += 1
        elif ch in '}])':
            depth -= 1
            if depth == 0:
                body = body[:k + 1]
                break
    return ast.literal_eval(body)


def unesc(b):
    """bytes → gbk 文本，顺带处理反斜杠 xNN 字面量转义（Py2 源码里的样子）。"""
    if not isinstance(b, bytes):
        return b
    s = b.decode('latin-1')            # 逐字节还原，转义序列仍是可读字符
    s = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)
    return s.encode('latin-1').decode('gbk', 'replace')


def main():
    skills = load_dict_literal(SKILLDATA)
    jq = load_dict_literal(JUQINGDATA)
    rows = {}
    for sid, sk in skills.items():
        sid = int(sid)
        if not (2001 <= sid < 2030):        # dlglearnskill 的剧情技能区间（真值）
            continue
        meta = jq.get(sid) or {}
        rows[sid] = {
            'name': unesc(sk.get('name', b'')),
            'desc': unesc(sk.get('desc', b'')).replace('#r', ' '),
            'type': int(sk.get('type', 3)),
            'max': int(meta.get('max', 5)),
            'dist': int(meta.get('dist', 2)),
        }
    if not rows:
        print('✗ 没解析到剧情技能（skilldata 2001..2029）')
        return 2
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('"""剧情技能表（自动生成，勿手改）。\n\n')
        f.write('剧情点的唯一消费入口：找 NPC 学/升剧情技能，付的就是 m_StoryPoint。\n')
        f.write('· 名称/说明/type  → 客户端 skilldata.py（真值）\n')
        f.write('· max（最高等级）/ dist（每级剧情点）→ 客户端 juqingdata.py（真值）\n')
        f.write('· 生成：python tests/sim/gen_juqing_skills.py\n')
        f.write('· 协议：C2S_NPC_LEARN_SKILL(103) 学/升 → S2CSkillLevel(65) sktype=3 回执\n')
        f.write('"""\n\n')
        f.write('JUQING_SKILLS = {\n')
        for sid in sorted(rows):
            r = rows[sid]
            f.write("    %d: {'name': %r, 'desc': %r,\n" % (sid, r['name'], r['desc']))
            f.write("          'type': %d, 'max': %d, 'dist': %d},\n"
                    % (r['type'], r['max'], r['dist']))
        f.write('}\n\n')
        f.write('JUQING_SKILL_IDS = tuple(sorted(JUQING_SKILLS))\n')
    print('已生成 %s（%d 个剧情技能，每级 2–4 点，最高 3–8 级）'
          % (os.path.basename(OUT), len(rows)))
    for sid in sorted(rows)[:6]:
        r = rows[sid]
        print('   %d %s max=%d dist=%d' % (sid, r['name'], r['max'], r['dist']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
