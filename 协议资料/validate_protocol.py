#!/usr/bin/env python3
"""梦想世界客户端协议资料只读审计脚本。

运行: python validate_protocol.py
默认从脚本所在目录读取原始七个文件，输出 protocol_audit_report.md。
不会修改原始 JSON 或服务器列表。
"""
from pathlib import Path
from collections import Counter, defaultdict
import json
import re
import ipaddress

ROOT = Path(__file__).resolve().parent

def load(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))

def safe_table(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')

def audit():
    combat = load('combat_opcodes_fields.json')
    itempet = load('itempet_opcodes_fields.json')
    fmap = load('protocol_map.json')
    funcs = load('protocol_functions.json')
    c2s_lines = (ROOT / 'protocol_c2s_full.txt').read_text(encoding='utf-8-sig').splitlines()
    s2c_lines = (ROOT / 'protocol_s2c_full.txt').read_text(encoding='utf-8-sig').splitlines()
    server_lines = (ROOT / '服务器列表.txt').read_text(encoding='utf-8-sig').splitlines()

    sections = ['# 协议资料自动审计报告', '', '> 此报告是静态元数据检查，不证明数据包在真实网络环境中正确。', '']
    flat = [(k, f) for k, v in funcs['modules'].items() for f in v.get('functions', [])]
    definitions = {**combat, **itempet}
    all_opcode_defs = fmap['c2s_opcodes']
    sections += [
        '## 1. 资料统计', '',
        f'- C2S/GS2C 定义项：**{len(all_opcode_defs)}**；不区分方向的不同编号：**{len(set(v["opcode"] for v in all_opcode_defs.values()))}**。',
        f'- S2C 路由：**{len(fmap["s2c_routes"])}**。',
        f'- 源码函数记录：**{len(flat)}**，其中直接标注 Opcode 的 C2S 函数：**{sum(f.get("dir")=="C2S" and f.get("opcode") is not None for _,f in flat)}**。',
        f'- 战斗字段函数：**{len(combat)}**；物品/宠物等字段函数：**{len(itempet)}**。',
        ''
    ]
    lines_c2s=[]
    for line in c2s_lines:
        m = re.match(r'^\s*(\d+)\s+(C2S_\S+|GS2C_\S+)\s+', line)
        if m: lines_c2s.append((m.group(2),int(m.group(1))))
    in_list=dict(lines_c2s)
    map_mismatch=[f'{name}: text={op} map={all_opcode_defs.get(name)}' for name,op in lines_c2s if all_opcode_defs.get(name,{}).get('opcode')!=op]
    map_absent=sorted(set(all_opcode_defs)-set(in_list))
    s2c_labels=[]
    for line in s2c_lines:
        m=re.match(r'^\s*(?:(\d+)\s+)?([A-Za-z_][\w]*\.[A-Za-z_][\w]*)\s+',line)
        if m: s2c_labels.append((int(m.group(1)) if m.group(1) else None,m.group(2)))
    map_routes={(x.get('tag'), x.get('module')+'.'+x.get('handler')) for x in fmap['s2c_routes']}
    list_routes=set(s2c_labels)
    sections += ['## 2. 文件间的一致性', '',
                 f'- C2S/GS2C 文本清单：{len(lines_c2s)} 条；相对 map 定义：编号不一致 {len(map_mismatch)} 条，map 未收录在文本清单的名称 {len(map_absent)} 条。',
                 f'- S2C 文本路由：{len(s2c_labels)} 条；相对 map 定义：仅文本存在 {len(list_routes-map_routes)} 条，仅 JSON 存在 {len(map_routes-list_routes)} 条。', '']
    if map_mismatch:
        sections += ['### C2S 映射不一致',''] + [f'- {x}' for x in map_mismatch] + ['']
    if map_absent:sections += ['### 文本中缺失的映射项',''] + [f'- {x}' for x in map_absent]+['']
    if list_routes-map_routes:sections += ['### S2C 文本独有路由','']+[f'- {tag}: {name}' for tag,name in sorted(list_routes-map_routes,key=str)]+['']
    if map_routes-list_routes:sections += ['### S2C JSON 独有路由','']+[f'- {tag}: {name}' for tag,name in sorted(map_routes-list_routes,key=str)]+['']

    groups=defaultdict(list)
    for name,f in definitions.items():groups[f['opcode']].append(name)
    sections += ['## 3. 复用 Opcode（需要保留多变体）', '', '| Opcode | 函数数 | 函数 |','|---:|---:|---|']
    for opcode,names in sorted(groups.items()):
        if len(names)>1:
            sections.append(f'| {opcode} | {len(names)} | {safe_table(", ".join(names))} |')
    sections+=['', '> 相同 Opcode 不一定是冲突：可能有子命令、不同参数形态或不同方向。', '']
    bad_fields=[];variable=[];unequal=[];no_map=[];no_funcs=[]
    for name,f in definitions.items():
        fixed = 0; dynamic=[]
        for i,x in enumerate(f['fields']):
            s=x.get('size')
            if type(s) is int and s>=0:fixed+=s
            else:
                dynamic.append((i,s,x.get('kind'),x.get('value')))
                if not (s=='N:len' and x.get('kind')=='S'):
                    bad_fields.append((name,f['opcode'],i,str(s),str(x.get('kind')),str(x.get('value'))))
        if dynamic:variable.append((name,f['opcode'],dynamic))
        if fixed != f['total_bytes']:unequal.append((name,f['opcode'],fixed,f['total_bytes']))
        const=all_opcode_defs.get(f['opcode_const'])
        if const is None or const.get('opcode')!=f['opcode']:no_map.append(name)
        if not any(x['name']==name and x.get('opcode')==f['opcode'] for _,x in flat):no_funcs.append(name)
    sections += ['## 4. 长度及字段异常', '',
                f'- 带有非固定长度字段的函数：**{len(variable)}**。',
                f'- 固定字段长度求和与 total_bytes 不一致：**{len(unequal)}**（注意：此项不覆盖可变内容）。',
                f'- 非标准动态字段描述：**{len(bad_fields)}**。',
                f'- 字段函数的 Opcode 映射缺失/冲突：**{len(no_map)}**；函数源码索引中找不到同名同编号定义：**{len(no_funcs)}**。', '',
                '| 函数 | Opcode | 位置 | size | kind | value |', '|---|---:|---:|---|---|---|']
    for name,op,i,sz,kind,val in bad_fields:sections.append(f'| {name} | {op} | {i} | {safe_table(sz)} | {safe_table(kind)} | {safe_table(val)} |')
    if not bad_fields:sections.append('| 无 | — | — | — | — | — |')
    sections += ['', '### 可变字段明细', '']
    for name,op,dyn in variable:
        sections.append(f'- `{name}` (Opcode {op}): {safe_table(dyn)}')
    sections += ['', '### 必须人工验证的高风险条目', '',
        '- **Opcode 34**：`C2SItemUse`（8 字节固定字段）、`C2SItemsUse`（4 字节）、`C2SItemSummonUse`（9 字节）；无统一显式子命令，应验证服务端如何区分。',
        '- **Opcode 176**：`C2SLookWarrior(order)` 与 `C2SWarLook(id)` 字段宽度相同但命名语义不同。',
        '- **Opcode 71 子命令 33**：`C2SArrange` 的 `size="param:data", value="N:len"` 不符合其他字段的表示方式；与函数索引中对 `len(data)` 的读取描述比对后再修改。',
        '- **Opcode 175**：`C2SSendSign` 包含可变长度字符串，但 `total_bytes=0` 不是完整包体长度。',
        '- **方向字段**：C2S 与 GS2C 可能共用编号 174、181，不能在不分方向的字典中覆盖。', '']

    sections += ['## 5. 服务器列表静态检查', '']
    records=[];current=None;errors=[]
    for line_no,line in enumerate(server_lines,1):
        if not line.strip():continue
        if line.startswith('[') and line.endswith(']'):
            current=line[1:-1];continue
        fields=line.split('\t')
        if len(fields)!=6:
            errors.append((line_no,'列数不等于 6',line));continue
        name,ip,port,flag,remark,alias=fields
        try:ipaddress.ip_address(ip)
        except ValueError:errors.append((line_no,'IP 字段不是单独的合法地址（疑似漏分隔符）',line))
        try:
            po=int(port)
            if not 1<=po<=65535:raise ValueError()
        except ValueError:errors.append((line_no,'端口非法',line))
        if port != '3134' and ip != '127.0.0.1':
            errors.append((line_no,f'非主流端口 {port}（需核实，不一定错误）',line))
        records.append((line_no,current,name,ip,port,flag,remark,alias))
    cnt=Counter((r[2],r[3],r[4]) for r in records)
    duplicates=[(k,n) for k,n in cnt.items() if n>1]
    sections += [f'- 区域分组：{len(set(r[1] for r in records))}；服务器记录：{len(records)}。',
                 f'- 名称/IP/端口完全重复的三元组：{len(duplicates)} 组（允许分区重复，但导入前需要定义去重规则）。',
                 f'- 格式或特殊端口警告：{len(errors)} 条。', '',
                 '| 行号 | 原因 | 原始内容 |', '|---:|---|---|']
    for line_no,reason,line in errors:sections.append(f'| {line_no} | {safe_table(reason)} | {safe_table(line)} |')
    sections += ['', '## 6. 建议的修复策略', '',
        '1. **只读保留原件**：不要自动覆盖任何源文件。',
        '2. **创建规范化消息定义**：以 `direction + opcode + 可选 subcmd/variant` 作为键。',
        '3. **将动态字段显式建模**：例如 `length_expression`、`length_prefix`、`encoding`，未知值保持 `null`。',
        '4. **验证包头/字节序/加密压缩**：不能从当前字段摘录中擅自确定。',
        '5. **以客户端源码+本地模拟服证据修复歧义**：对高风险函数建立一条请求与对应响应的回归用例。',
        '6. **分离战斗行为与数值规则**：Opcode 决定操作和序列化，不足以给出伤害、成长、命中等公式。', '']
    report=ROOT/'protocol_audit_report.md'
    report.write_text('\n'.join(sections)+'\n',encoding='utf-8')
    print(f'报告已生成：{report}')
    print(f'已检查 {len(definitions)} 个字段函数，{len(variable)} 个包含变长字段，{len(bad_fields)} 个描述异常。')
    print(f'服务器 {len(records)} 条；标记 {len(errors)} 项格式或端口异常。')

if __name__=='__main__':
    audit()
