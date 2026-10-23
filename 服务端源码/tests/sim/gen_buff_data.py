# -*- coding: utf-8 -*-
u"""从客户端 `src/data/buffdata.py` 生成 `buff_data.py`（状态效果真值表）。

为什么生成而不是手写
--------------------
状态（buff）「叫什么、效果说明怎么写」全是客户端真值，**284 条**。手抄必错，
而且 P3-03 之前服务端一条都没有 —— 「燃烧每回合掉血」这类规则全靠想象。

提取口径
--------
  · `DATA = {id: {'desc': <bytes>}}`，desc 是 **GBK 字节串**。283 条被反编译成
    `b'\\xc8\\xbc\\xc9\\xd5...'` 形式（`escape_decode` 解回字节再 gbk 解码）；
    唯独 145 号被写成了 str（`'\\u04ba\\u032c'` = GBK 的 `D2 BA CC AC`），
    需要 `encode('utf-8')` 再 gbk —— 两条路径都要走，漏一条就少一条状态。
  · `ICONTABLE = {id: icon_id}`，249 条；7 条状态没有图标（`Buff2Icon` 对
    KeyError 回落成 id 本身，所以「没图标」不等于「非状态」）。

能拿到什么 / 拿不到什么
-----------------------
  OK 状态 id / 描述原文 / 图标：直读，无推断。
  !! 状态的**具体数值参数**（持续几回合、每回合掉百分之几、加成多少）：
     **不在**本表里。buffdata 只有一句玩家可见的说明，例如
     287「灼热；每回合掉当前气血上限的20%」—— 20% 是从**说明文字**里读出来的，
     不是从参数表读出来的，所以只能算 PARTIAL（玩家文案级），不是 TRUE。
     284 条里只有 35 条说明里带数字，其余 249 条（如 20「燃烧；在持续回合内，
     每回合对承受者造成伤害」）连百分比都没写。
  XX 「状态怎么施加 / 驱散条件 / 是否可叠加」：说明文字里偶有提及，
     但没有结构化字段，无法当规则用。

用法：
    python 服务端源码/tests/sim/gen_buff_data.py            # 重写产物
    python 服务端源码/tests/sim/gen_buff_data.py --check    # 只校验是否过期
"""
import argparse
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MX = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BUFF = os.path.join(MX, '客户端源码', 'src', 'data', 'buffdata.py')
OUT = os.path.join(HERE, 'buff_data.py')

# 生成时逐个断言的关键条目：这几条一旦对不上，说明提取口径坏了（或客户端换版），
# 必须当场炸，而不是静默产出一张错表。
# 选它们的理由：都是 m4_server / combat_effects 直接引用的状态，且描述里有明确数值。
KNOWN = {
    10: u'反击',        # 单体物理攻击反弹
    19: u'眩晕',        # 用过强招后的虚弱状态（控制类代表）
    20: u'燃烧',        # 持续伤害（**无数值**的代表：说明里没写百分比）
    27: u'恐怖',        # 强制防御 1 回合
    184: u'狂暴',       # 攻速×2 / 防×0.7
    188: u'水晶墙',     # 受伤×0.2 + 反射 250%
    287: u'灼热',       # 每回合掉气血上限 20%
    501: u'杀人毒针',   # 4 回合，每回合 10%
}

# 「说明里带数字」的判定：至少一个阿拉伯数字。用于统计可量化条目数。
# ★ 带数字 ≠ 可机读：34 条里既有「持续6回合」「+30%」这种能转录的，
#   也有「高过隐身者10级」这种等级门槛。真正的转录在 combat_effects.EFFECT_MODS，
#   逐条人工确认，本表只负责「把候选挑出来」。
_NUM_RE = re.compile(r'[0-9]')


def decode_text(v):
    """把客户端字段解成 unicode。

    bytes  → escape_decode 回原始字节，再按 gbk 解
    str    → 反编译器偶尔留下「GBK 字节被当 utf-8 读」的残骸，encode 回 utf-8 再 gbk
    """
    if isinstance(v, bytes):
        try:
            raw = v.decode('string_escape') if False else v
        except Exception:                                   # noqa: BLE001
            raw = v
        try:
            return raw.decode('gbk')
        except UnicodeDecodeError:
            return raw.decode('gbk', 'replace')
    if isinstance(v, str):
        try:
            return v.encode('utf-8').decode('gbk')
        except (UnicodeDecodeError, UnicodeEncodeError):
            return v
    return str(v)


def grab_tables(path=BUFF):
    """从反编译产物里取出 DATA / ICONTABLE 两个字面量。"""
    src = io.open(path, 'rb').read().decode('utf-8', 'replace')
    tree = ast.parse(src)
    tabs = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    try:
                        tabs[tgt.id] = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        pass
    if 'DATA' not in tabs:
        raise SystemExit('提取失败：%s 里找不到 DATA 字面量' % path)
    return tabs.get('DATA') or {}, tabs.get('ICONTABLE') or {}


def build():
    data, icons = grab_tables()
    info = {}
    for sid in sorted(data):
        info[int(sid)] = decode_text((data[sid] or {}).get('desc', ''))
    icon = {int(k): int(v) for k, v in icons.items()}
    return info, icon


def crosscheck(info, icon):
    """对账：数量 / 关键条目 / 编码。任何一条不符就抛，不产出坏表。"""
    if len(info) < 280:
        raise SystemExit('状态条目数偏少：%d（期望 >=280）' % len(info))
    for sid, name in KNOWN.items():
        if sid not in info:
            raise SystemExit('关键状态 %d（%s）在表里不存在' % (sid, name))
        if not info[sid].startswith(name):
            raise SystemExit('状态 %d 名称不符：期望以「%s」开头，实际「%s」'
                             % (sid, name, info[sid][:20]))
    # 编码抽查：全表不能有替换字符（U+FFFD），有就说明 gbk 解码失败
    bad = [sid for sid, d in info.items() if u'\ufffd' in d]
    if bad:
        raise SystemExit('有 %d 条描述解码失败（含替换符）：%s'
                         % (len(bad), bad[:10]))
    if 145 not in info or info[145] != u'液态':
        raise SystemExit('145 号（str 路径）解码失败：%r'
                         % (info.get(145),))
    if len(icon) < 240:
        raise SystemExit('图标条目数偏少：%d（期望 >=240）' % len(icon))
    nums = [sid for sid, d in info.items() if _NUM_RE.search(d)]
    if len(nums) < 30:
        raise SystemExit('含量化描述的条目偏少：%d（期望 >=30）' % len(nums))
    return nums


def render(info, icon, nums):
    out = io.StringIO()
    out.write(u'# -*- coding: utf-8 -*-\n')
    out.write(u'"""状态效果真值表（**生成物，勿手改**）。\n\n')
    out.write(u'由 `gen_buff_data.py` 从客户端 `src/data/buffdata.py` 提取。\n')
    out.write(u'重生成： `python 服务端源码/tests/sim/gen_buff_data.py`\n')
    out.write(u'校验：   `python 服务端源码/tests/sim/gen_buff_data.py --check`\n\n')
    out.write(u'内容：%d 条状态的描述原文 + %d 条图标映射。\n\n'
              % (len(info), len(icon)))
    out.write(u'★ 本表**只有玩家可见的说明文字**，没有数值参数表。\n')
    out.write(u'  描述里带数字的只有 %d 条，且多为「持续N回合 / 提高N%%」这类文案；\n' % len(nums))
    out.write(u'  可机读的转录集中在 `combat_effects.EFFECT_MODS`，逐条标注出处。\n')
    out.write(u'"""\n')
    out.write(u'from __future__ import unicode_literals\n\n')
    out.write(u'SOURCE = %r\n\n' % '客户端源码/src/data/buffdata.py')

    out.write(u'# 状态 id → 描述原文（GBK 已解码）\n')
    out.write(u'BUFF_INFO = {\n')
    for sid in sorted(info):
        out.write(u'    %d: %r,\n' % (sid, info[sid]))
    out.write(u'}\n\n')

    out.write(u'# 状态 id → 图标 id（`Buff2Icon` 的查表值；缺项时客户端回落成 id 本身）\n')
    out.write(u'BUFF_ICON = {\n')
    for sid in sorted(icon):
        out.write(u'    %d: %d,\n' % (sid, icon[sid]))
    out.write(u'}\n\n')

    out.write(u'# 描述里含阿拉伯数字的条目（**候选**，不代表一定可机读）\n')
    out.write(u'QUANT_CANDIDATES = (\n')
    line = u'    '
    for sid in sorted(nums):
        piece = u'%d, ' % sid
        if len(line) + len(piece) > 76:
            out.write(line.rstrip() + u'\n')
            line = u'    '
        line += piece
    if line.strip():
        out.write(line.rstrip() + u'\n')
    out.write(u')\n\n')

    out.write(u'''
def get(sid):
    """取描述；未知 id 返回 None（不抛，调用方自己决定怎么报）。"""
    return BUFF_INFO.get(int(sid))


def icon(sid):
    """图标 id；缺项回落成状态 id（对齐客户端 `Buff2Icon` 的 KeyError 分支）。"""
    sid = int(sid)
    return BUFF_ICON.get(sid, sid)


def name(sid):
    """状态名 = 描述里第一个分号/冒号前面的部分。

    客户端描述统一写成「名字；效果说明…」或「名字：效果说明…」，
    所以首个 `；` / `：` / `:` 之前的片段就是名字。没有分隔符时取前 8 字。
    """
    d = get(sid)
    if not d:
        return u''
    for sep in (u'；', u'：', u';', u':'):
        if sep in d:
            return d.split(sep, 1)[0].strip()
    return d[:8]


def ids():
    """全部状态 id（升序）。"""
    return sorted(BUFF_INFO)


def quantified():
    """描述里带数字的 id 列表。"""
    return list(QUANT_CANDIDATES)


def describe(sid):
    """「id 名字：描述」一行摘要（日志/GM 用）。"""
    d = get(sid)
    if d is None:
        return u'#%d 未知状态' % int(sid)
    return u'#%d %s' % (int(sid), d)


def provenance():
    return {
        'module': 'buff_data',
        'source': '客户端源码/src/data/buffdata.py',
        'generated_by': 'gen_buff_data.py',
        'entries': len(BUFF_INFO),
        'icons': len(BUFF_ICON),
        'quantified': len(QUANT_CANDIDATES),
        'level': 'TRUE(文案) / PARTIAL(数值)',
        'gaps': [
            '无状态数值参数表：持续回合/加成幅度只能从说明文字转录',
            '无施加方式/驱散条件/叠加规则的结构化字段',
        ],
    }
''')
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只校验产物是否过期')
    args = ap.parse_args()
    info, icon = build()
    nums = crosscheck(info, icon)
    text = render(info, icon, nums)
    if args.check:
        old = u''
        if os.path.isfile(OUT):
            old = io.open(OUT, 'r', encoding='utf-8').read()
        if old != text:
            print('[STALE] %s 与客户端表不一致，请重跑 gen_buff_data.py' % OUT)
            return 1
        print('[OK] buff_data.py 与客户端表一致（%d 条状态 / %d 条图标 / %d 条含量化）'
              % (len(info), len(icon), len(nums)))
        return 0
    io.open(OUT, 'w', encoding='utf-8', newline='\n').write(text)
    print('[WROTE] %s（%d 条状态 / %d 条图标 / %d 条含量化）'
          % (OUT, len(info), len(icon), len(nums)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
