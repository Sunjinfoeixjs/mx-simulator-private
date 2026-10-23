# -*- coding: utf-8 -*-
u"""从客户端 `data/petskill.py` 生成 `pet_skill_data.py`（宠物技能真值表）。

为什么生成而不是手写
--------------------
宠物技能「叫什么、是主动还是被动、消耗多少 FP/MP、描述写的是什么」全部是客户端
真值，404 条，手抄必错；而且 `m4_server` 里那些「desc 写『必杀几率提高8%』」
之类的注释，**只有对着原表才能验证**。

提取口径
--------
  · `DATA` 是 `{id: {'type','name','desc','fpneed','mpneed','hpneed'}}`，
    字符串字段是 **GBK 字节串**（`b'\\xc1\\xac\\xbb\\xf7'` = 「连击」），
    必须 `escape_decode` 解回字节再用 gbk 解码 —— 直接 grep 中文搜不到，
    直接当 utf-8 读会得到乱码。
  · `type`：0=被动 / 1=主动攻击 / 2=主动辅助。客户端
    `warui.CSummonMagicDialog` 只把 `type == 1 or type == 2` 放进宠物招式菜单，
    被动技能根本不会出现在菜单里 —— 这条事实被 `_pet_perform` 用来区分
    「非法指令」与「未实现招式」。
  · `fpneed` / `mpneed` / `hpneed`：客户端 `pet.PetMagicInvalid` 的表达式原文
    （如 415 的 `FP-(15+LV/3+3*5)`）。本模块保留**原文**，求值仍由调用方
    用 `py2_expr`（Py2 整除语义，别用 Python 3 的 `/`）。

真值 / 缺口
-----------
  OK 名称/类型/描述/消耗：全部直读，无推断。
  XX 「魔兽要诀 → 学会哪个技能」：**不可得**。itemdata 只有单一道具 25405，
     没有「每本要诀记载哪个技能」的字段，所以本表只能回答「技能是什么」，
     不能回答「用某本要诀会学到什么」（见 pet_skill_service.SKILL_SRC_GAP）。

用法：
    python 服务端源码/tests/sim/gen_pet_skill_data.py            # 重写产物
    python 服务端源码/tests/sim/gen_pet_skill_data.py --check    # 只校验是否过期
"""
import argparse
import ast
import codecs
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PETSKILL = os.path.join(MX, '客户端源码', 'src', 'data', 'petskill.py')
OUT = os.path.join(HERE, 'pet_skill_data.py')

SKILL_TYPES = {0: u'被动', 1: u'主动攻击', 2: u'主动辅助'}

# 录像里出现过、被 m4_server 直接引用的技能 —— 生成时逐个断言存在且类型对。
# 这几条一旦对不上，说明提取口径坏了（或客户端换版），必须炸而不是静默产出。
KNOWN = {
    415: (1, u'乱舞冲击'),      # 录像 v_23 第 4 格 tooltip「（主动）群体物理攻击」
    505: (0, u'恐怖'),          # 录像 v_22 第 6 格 tooltip「（被动）…10% 几率」
    112: (0, u'必杀'),
    102: (0, u'连击'),
    317: (0, u'终级迅捷'),
    319: (0, u'终级强力'),
}


def decode_text(v):
    """客户端 GBK 字节串 → unicode。

    `b'\\xNN'` 在源码里是 bytes 字面量，直接 `.decode('gbk')` 就行；
    但个别字段被反编译成了 str 形式的转义（`'\\xNN'`），那种要先
    `escape_decode` 回到字节。两种都吃，避免逐条判类型。
    """
    if isinstance(v, bytes):
        return v.decode('gbk', 'replace')
    if isinstance(v, str):
        if '\\x' not in v:
            return v
        try:
            return codecs.escape_decode(v.encode('latin-1'))[0].decode('gbk', 'replace')
        except (UnicodeDecodeError, ValueError):
            return v
    return u'' if v is None else str(v)


def _grab_data_literal(src):
    """取 `DATA = {...}` 的平衡括号文本。

    `petskill.py` 是反编译产物，顶部有 mojibake（`GetData` 里的 'δ֪'），
    所以只对 DATA 这一段做 `ast.literal_eval`，不 exec 整个文件。
    """
    i = src.find('DATA = ')
    if i < 0:
        raise AssertionError('客户端 petskill.py 里找不到 DATA')
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
    raise AssertionError('DATA 括号不配平')


def load_data():
    """→ {sid: (name, type, desc, fpneed, mpneed, hpneed)}"""
    # 反编译源码整体不是合法 utf-8（顶部有 GBK 注释），但 DATA 段是纯 ASCII，
    # 用 replace 读进来不影响字面量解析。
    src = io.open(PETSKILL, encoding='utf-8', errors='replace').read()
    raw = ast.literal_eval(_grab_data_literal(src))
    out = {}
    for sid, d in raw.items():
        try:
            tid = int(d.get('type', 0))
        except (TypeError, ValueError):
            tid = 0
        out[int(sid)] = (decode_text(d.get('name')),
                         tid,
                         decode_text(d.get('desc')),
                         str(d.get('fpneed') or '0'),
                         str(d.get('mpneed') or '0'),
                         str(d.get('hpneed') or '0'))
    return out


def crosscheck(rows):
    """对账：条目数、已知技能的 id/类型/名字、类型取值域。

    这不是「测试」，是生成器的**自检**——产物一旦悄悄少一条，后面所有
    「这个技能叫什么」的显示都会变成 `未知<id>`，而且没人会发现问题。
    """
    if len(rows) < 400:
        raise AssertionError('技能条目只有 %d 条，明显不完整' % len(rows))
    for sid, (want_type, want_name) in KNOWN.items():
        if sid not in rows:
            raise AssertionError('客户端 petskill 里找不到技能 %d（%s）'
                                 % (sid, want_name))
        name, tid = rows[sid][0], rows[sid][1]
        if tid != want_type:
            raise AssertionError('技能 %d 的 type=%d，期望 %d' % (sid, tid, want_type))
        if name != want_name:
            raise AssertionError('技能 %d 的名字是 %r，期望 %r'
                                 % (sid, name, want_name))
    bad = sorted({v[1] for v in rows.values()} - set(SKILL_TYPES))
    if bad:
        raise AssertionError('出现未登记的技能类型 %r' % bad)
    if not rows[415][2] == u'群体物理攻击':
        raise AssertionError('415 的 desc 是 %r，期望「群体物理攻击」' % (rows[415][2],))


def render(rows):
    n_type = {}
    for v in rows.values():
        n_type[v[1]] = n_type.get(v[1], 0) + 1
    lines = []
    lines.append(u'# -*- coding: utf-8 -*-')
    lines.append(u'"""宠物技能真值表（**生成物，勿手改**）。')
    lines.append(u'')
    lines.append(u'由 `gen_pet_skill_data.py` 从客户端 `data/petskill.py` 的 `DATA` 提取：')
    lines.append(u'  · 名称/描述：GBK 字节串 → unicode（不是 utf-8，别直接读）')
    lines.append(u'  · type：0=被动 / 1=主动攻击 / 2=主动辅助')
    lines.append(u'  · fpneed/mpneed/hpneed：客户端 `PetMagicInvalid` 的表达式**原文**')
    lines.append(u'')
    lines.append(u'⚠ 本表只回答「技能是什么」，**不回答**「哪本魔兽要诀教它」——')
    lines.append(u'  那个映射在 itemdata 里不存在（只有单一道具 25405），属证据缺口。')
    lines.append(u'  见 pet_skill_service.SKILL_SRC_GAP。')
    lines.append(u'')
    lines.append(u'共 %d 条：被动 %d / 主动攻击 %d / 主动辅助 %d'
                 % (len(rows), n_type.get(0, 0), n_type.get(1, 0),
                    n_type.get(2, 0)))
    lines.append(u'"""')
    lines.append(u'')
    lines.append(u'SOURCE = %r' % ('客户端源码/src/data/petskill.py · DATA'))
    lines.append(u'')
    lines.append(u'# type → 中文（客户端只把 1/2 放进宠物招式菜单，0 是被动）')
    lines.append(u'SKILL_TYPES = {')
    for k in sorted(SKILL_TYPES):
        lines.append(u'    %d: %r,' % (k, SKILL_TYPES[k]))
    lines.append(u'}')
    lines.append(u'')
    lines.append(u'# sid: (名称, type, 描述, fpneed, mpneed, hpneed)')
    lines.append(u'PET_SKILL_INFO = {')
    for sid in sorted(rows):
        name, tid, desc, fp, mp, hp = rows[sid]
        lines.append(u'    %d: (%r, %d, %r, %r, %r, %r),'
                     % (sid, name, tid, desc, fp, mp, hp))
    lines.append(u'}')
    lines.append(u'')
    lines.append(u'')
    lines.append(u'def get(sid, limit=80):')
    lines.append(u'    """按 id 取技能信息；未知 id 返回 None（不抛、不编造名字）。"""')
    lines.append(u'    try:')
    lines.append(u'        info = PET_SKILL_INFO.get(int(sid))')
    lines.append(u'    except (TypeError, ValueError):')
    lines.append(u'        return None')
    lines.append(u'    if info is None:')
    lines.append(u'        return None')
    lines.append(u'    name, tid, desc, fp, mp, hp = info')
    lines.append(u'    return {')
    lines.append(u"        'sid': int(sid), 'name': name, 'type': tid,")
    lines.append(u"        'type_name': SKILL_TYPES.get(tid, u'?'),")
    lines.append(u"        'desc': desc[:limit], 'fpneed': fp, 'mpneed': mp, 'hpneed': hp,")
    lines.append(u'    }')
    lines.append(u'')
    lines.append(u'')
    lines.append(u'def known(sid):')
    lines.append(u'    """id 是否在本版客户端的技能表里（权威白名单）。"""')
    lines.append(u'    try:')
    lines.append(u'        return int(sid) in PET_SKILL_INFO')
    lines.append(u'    except (TypeError, ValueError):')
    lines.append(u'        return False')
    lines.append(u'')
    lines.append(u'')
    lines.append(u'def active_ids():')
    lines.append(u'    """能在宠物招式菜单里出现的 id（type 1/2）；被动不算。"""')
    lines.append(u'    return sorted(k for k, v in PET_SKILL_INFO.items() if v[1] in (1, 2))')
    lines.append(u'')
    return u'\n'.join(lines)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只校验产物是否过期')
    args = ap.parse_args(argv)

    rows = load_data()
    crosscheck(rows)
    text = render(rows)
    old = io.open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else u''
    if args.check:
        if old != text:
            print(u'pet_skill_data.py 已过期，请重跑 gen_pet_skill_data.py')
            return 1
        print(u'pet_skill_data.py 与客户端一致（%d 条技能）' % len(rows))
        return 0
    io.open(OUT, 'w', encoding='utf-8', newline='\n').write(text)
    n_type = {}
    for v in rows.values():
        n_type[v[1]] = n_type.get(v[1], 0) + 1
    print(u'已写出 %s' % OUT)
    print(u'  技能 %d 条：被动 %d / 主动攻击 %d / 主动辅助 %d'
          % (len(rows), n_type.get(0, 0), n_type.get(1, 0), n_type.get(2, 0)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
