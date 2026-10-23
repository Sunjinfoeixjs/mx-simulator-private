# -*- coding: utf-8 -*-
"""一次性解析 docx 主线 7–22 流程与 NPC 坐标表 → JSON（供人工核对后写入 quest_data）。"""
import io
import json
import os
import re
import sys

SRC = r'C:\Users\Administrator\MX\客户端数据\_docx_text.txt'
OUT = os.path.join(os.environ.get('TEMP', '.'), 'quest_flows.json')


def clean_name(s):
    return re.sub(r'\s+', '', s)


def main():
    text = io.open(SRC, encoding='utf-8', errors='replace').read()
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == '7、并雄的四国':
            start = i
            break
    if start is None:
        print('not found')
        return 1
    seg = lines[start:]
    end = None
    for i, ln in enumerate(seg):
        if re.match(r'^23、', ln.strip()):
            end = i
            break
    seg = seg[:end] if end else seg

    quests = []
    seen = set()
    cur = None
    mode = None
    for ln in seg:
        s = ln.strip()
        m = re.match(r'^(\d+)、(.+)$', s)
        if m and int(m.group(1)) <= 22:
            no = int(m.group(1))
            if no in seen:                 # 同名块在别的章节再出现：忽略后续
                cur = None
                mode = None
                continue
            seen.add(no)
            cur = {'no': no, 'name': clean_name(m.group(2)),
                   'place': '', 'require': '', 'flow': '', 'npc_rows': []}
            quests.append(cur)
            mode = None
            continue
        if cur is None:
            continue
        if s.startswith('领取地点'):
            cur['place'] = s.split('：', 1)[1].replace(' ', '')
            continue
        if s.startswith('领取要求'):
            cur['require'] = s.split('：', 1)[1].replace(' ', '')
            continue
        if s.startswith('剧情流程'):
            mode = 'flow'
            rest = s.split('：', 1)[1] if '：' in s else ''
            cur['flow'] += rest
            continue
        if s == 'NPC':
            mode = 'npc'
            continue
        if s.startswith('回顶部'):
            mode = None
            continue
        if mode == 'flow':
            cur['flow'] += s
        elif mode == 'npc':
            if set(s) <= set('| '):
                continue
            if s.startswith('|'):
                if cur['npc_rows'] and cur['npc_rows'][-1][1] is None:
                    cur['npc_rows'][-1][1] = s.lstrip('|').strip().replace(' ', '')
                continue
            if s.startswith('位置'):
                continue
            cur['npc_rows'].append([clean_name(s), None])

    out = []
    for q in quests:
        flow = q['flow'].replace('→ →', '→→')
        tokens = [clean_name(t) for t in flow.split('→→') if t.strip()]
        rows = []
        for name, coord in q['npc_rows']:
            mm = re.match(r'^(.+?)（(\d+)[,，](\d+)）$', coord or '')
            if mm:
                rows.append({'npc': name, 'map': mm.group(1).strip(),
                             'x': int(mm.group(2)), 'y': int(mm.group(3))})
            else:
                rows.append({'npc': name, 'map': coord, 'x': None, 'y': None})
        q['flow_tokens'] = tokens
        q['npc_table'] = rows
        del q['flow']
        del q['npc_rows']
        out.append(q)

    io.open(OUT, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, ensure_ascii=False, indent=1))
    for q in out:
        print('%2d %-8s %s | 步骤 %d | NPC表 %d'
              % (q['no'], q['name'], q['place'], len(q['flow_tokens']),
                 len(q['npc_table'])))
    print('total', len(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
