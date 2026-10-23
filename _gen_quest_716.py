# -*- coding: utf-8 -*-
"""把 docx 的 7–16 流程 + NPC 坐标表，编译成本服 quest_data 的数据结构。

真值来源
  · 流程 / NPC 坐标：《梦想世界2013资料全库》7–16 每条「流程」+ 末尾 NPC 坐标表
    （已由 _parse_quests.py 解析到 %TEMP%/quest_flows.json）
  · 世界 NPC 坐标与造型：客户端 src/data/npcposdata.py（行 = 名字/·/造型/类型/(x,y)/…）
  · 等级 / 剧情点 / 触发 NPC / 场景：协议资料/网上资料_20261022/quests_mainline.json
  · 物品 id：客户端 src/data/itemdata.py

输出（不直接改 quest_data.py）：
  %TEMP%/quest_716_steps.json   —— 结构化步骤，供人工核对
  _quest_716_src.txt            —— 可贴进 quest_data.py 的 Python 片段
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

MX = r'C:\Users\Administrator\MX'
sys.path.insert(0, MX)
sys.path.insert(0, os.path.join(MX, '服务端源码', 'tests', 'sim'))

from _find_npc_pos import load as load_npcpos      # noqa: E402

TMP = os.environ.get('TEMP') or os.environ.get('TMP')

# ── 本服地图：名字 → mapid ─────────────────────────────────────────────────
WC = os.path.join(MX, '服务端源码', 'tests', 'sim', 'world_config.json')
_wc = json.load(io.open(WC, encoding='utf-8'))
MAP_BY_NAME = dict((m['name'], m['mapid']) for m in _wc['world']['maps'])
MAP_NAME = dict((m['mapid'], m['name']) for m in _wc['world']['maps'])

# docx 场景名 → 本服场景名（不改真值，只在数据里注明来源差异）
ALIAS = {
    u'清风村': u'清风村2',        # 客户端 dlg12star 记 1025=清风村；本库同图名为「清风村2」
    u'珊瑚迷宫二层': u'珊瑚迷宫',    # 客户端地图图只有单张珊瑚迷宫(1036)，无「二层」
}
NAME_ALIAS = {u'神隐护卫': u'神隐侍卫'}     # 流程写「护卫」、坐标表写「侍卫」，同一人

# ── 剧情物品（shape, 客户端精确名）────────────────────────────────────────
ITEM = {
    u'丝绸手绢': (25212, u'丝绸手绢'),
    u'神隐护腕': (63040, u'神隐护腕'),
    u'大力丸': (21108, u'大力丸'),
    u'荧光草': (25008, u'荧光草'),
    u'火神花': (21213, u'火神花'),
    u'七星戒指': (65040, u'七星戒指'),
    u'京剧脸谱': (25210, u'京剧脸谱'),
    u'紫砂茶壶': (25220, u'紫砂茶壶'),
    u'回命散': (21110, u'回命散'),
    u'驱邪丸': (21111, u'驱邪丸'),
    u'甘草': (21233, u'甘草'),
    u'仙灵石': (26010, u'仙灵石'),
}
# 资料要求、但本版 itemdata 查无的交付物 → 该步降级为对话（不造假 id，也不卡死）
ITEM_MISSING = {u'刃虎宝宝', u'大螃蟹宝宝'}

# ── 奖励物品：资料名 → (shape, 客户端名, note)；shape=None = 本版查无 ──────
REWARD = {
    u'空白经验心得': (None, u'空白经验心得',
                 u'本版 itemdata 查无此物，奖励记空（与 1–6 同口径）'),
    u'1级宝石包': (72063, u'一级如意宝石包',
                u'资料写「1级宝石包」，本版对应「一级如意宝石包」'),
    u'2级宝石包': (26800, u'二级如意宝石包',
                u'资料写「2级宝石包」，本版对应「二级如意宝石包」'),
    u'3级如意宝石包': (26801, u'三级如意宝石包', u'资料真值'),
    u'3级大点金石': (25017, u'大点金石',
                 u'资料写「3级大点金石」，本版只有「大点金石」，无分级'),
    u'属性重置丹': (30042, u'属性重置丹', u'资料真值'),
    u'40级召唤兽装备': (None, u'40级召唤兽装备',
                  u'本版 itemdata 无 40 级召唤兽装备（只有 80 级），奖励记空'),
    u'80级召唤兽装备': (30482, u'随机一件80级召唤兽装备',
                  u'资料写「80级召唤兽装备」，本版为「随机一件80级召唤兽装备」'),
    u'40级～50级招式启蒙': (None, u'40级～50级招式启蒙',
                     u'本版只有「XX名师启蒙」逐招式条目，无通用启蒙本，奖励记空'),
    u'高级魔兽要诀': (25405, u'魔兽要诀',
                 u'资料写「高级魔兽要诀」，本版无分级前缀，取「魔兽要诀」'),
    u'终极魔兽要诀': (25405, u'魔兽要诀',
                 u'资料写「终极魔兽要诀」，本版无分级前缀，取「魔兽要诀」'),
    u'宠物作战心得': (25404, u'宠物作战心得', u'资料真值'),
    u'60级打造装备': (None, u'60级打造装备',
                 u'本版只有各级「打造设计图」，无成品装备，奖励记空'),
    u'高级藏宝图': (25076, u'高级藏宝图', u'资料真值'),
    u'还童丹': (30026, u'还童丹', u'资料真值'),
    u'优质奇香果': (25433, u'优质奇香果', u'资料真值'),
    u'80级制造指南书': (25077, u'制造指南书',
                  u'资料写「80级制造指南书」，本版只有通用「制造指南书」'),
    u'百炼精铁': (25078, u'百炼精铁', u'资料真值'),
}
IGNORE_REWARD = (u'领悟招式', u'领悟主修')      # 走 learn_magic，不是物品

# ── 剧情 NPC 造型（SIM）：人类复用世界 NPC 造型，怪物用 monsterdata 近义造型 ──
SHAPE = {
    u'孙秀才': (4347, u'王捕头'), u'海外商人': (4183, u'铁匠'),
    u'花王李': (4202, u'鲁大婶'), u'金大有副手': (4283, u'龙校尉'),
    u'巧手张': (4183, u'铁匠'), u'李大娘': (4202, u'鲁大婶'),
    u'钱捕头': (4347, u'王捕头'), u'冯大叔': (4118, u'老农夫'),
    u'江湖一阵风': (4273, u'张二贵'), u'船夫': (4273, u'张二贵'),
    u'无面': (4330, u'护林长老'), u'小明明': (4118, u'老农夫'),
    u'公孙大娘': (4202, u'鲁大婶'), u'胡铁匠': (4183, u'铁匠'),
    u'唐师傅': (4183, u'铁匠'), u'跑堂小二': (4273, u'张二贵'),
    u'张猎户': (4118, u'老农夫'), u'巫道徒': (4331, u'汪太公'),
    u'兔工匠': (4332, u'巴容儿'), u'王医仙': (4330, u'护林长老'),
    u'药僮': (4273, u'张二贵'), u'英雄会接待人': (4349, u'钱梦庵'),
    u'刀狼大人': (2220, u'狼人'), u'沙漠行商': (2100, u'强盗'),
    u'青衣': (4202, u'鲁大婶'), u'胡半仙': (4331, u'汪太公'),
    u'碎梦老人': (4331, u'汪太公'), u'大祭祀': (4329, u'主教'),
    u'锦毛鼠': (2070, u'金花鼠'), u'铃铛猫女': (2630, u'狐美人'),
    u'马群': (2130, u'狼'), u'兔保镖': (2350, u'兔勇士'),
    u'追债打手': (2100, u'强盗'), u'狼贼先锋': (2490, u'影狼'),
    u'狼贼小分队': (2210, u'霜月狼'), u'狼贼大部队': (2500, u'狼勇士'),
    u'狼钻风': (2220, u'狼人'), u'江洋大盗': (2100, u'强盗'),
    u'恶角大魔头': (2530, u'大力鬼王'), u'利爪僵尸': (2450, u'镰刀僵尸'),
    u'斧牛妖': (2310, u'牛妖'), u'魔化仙子': (2330, u'森林仙子'),
    u'棕毛熊': (2370, u'怒熊'), u'千年古树精': (2410, u'古树精'),
    u'兔巫医': (2200, u'兔祭司'), u'红魔蝎': (2430, u'魔蜘蛛'),
    u'朱雀火鸟': (2380, u'烈火鸟'), u'蓝皮狂蟒': (2400, u'巨蟒'),
    u'狼武使者': (2500, u'狼勇士'), u'狼高手': (2220, u'狼人'),
    u'烈阳芒': (2660, u'炎魔'), u'花蜘蛛': (2430, u'魔蜘蛛'),
    u'暗影僵尸': (2280, u'僵尸'), u'飞翼虎': (2420, u'翼虎'),
    u'青蛙剑枭': (2460, u'青蛙侠客'), u'冥鬼骑士': (2440, u'死亡骑士'),
    u'叛变敢死狼': (2730, u'灰灰狼'), u'沙盗·南天霸': (2120, u'山贼'),
    u'神隐侍卫': (2470, u'神兵'),
}

# ── 解析用正则 ────────────────────────────────────────────────────────────
FIGHT_RE = re.compile(u'(单挑|组队|单人|必败)战斗|两场战斗')
ITEM_RE = re.compile(u'需要物品：([^，）]+)')
POINT_RE = re.compile(u'剧情点(\\d+)点')
REWARD_RE = re.compile(u'奖励：(.+?)(?:，若已领悟|，注|，完成|，剧情点|$)')
CN_NUM = {u'一': 1, u'二': 2, u'两': 2, u'三': 3, u'四': 4, u'五': 5,
          u'六': 6, u'七': 7, u'八': 8, u'九': 9, u'十': 10}
QTY_RE = re.compile(u'(\\d+|[一二两三四五六七八九十])\\s*'
                    u'(本|个|件|张|颗|只|棵|套|瓶)?$')

INTRO = {
    7: (u"P\n四国并雄，各守一方。眼下边境上风声不对，你既然有这个身手，就替我走一圈。\nQ我接下。\nQ再想想。",
        u"P\n先去海底神殿见加洛国王，他手里有第一桩。\nQ这就去。"),
    8: (u"P\n芳草镇外来了群兔族，说是逃难来的，可镇上的东西一天天少。你替我看看。\nQ我去查。\nQ改日。",
        u"P\n先找船夫问一句，兔族是从水路来的。\nQ这就去。"),
    9: (u"P\n北边的狼族最近不安分，商队已经折了两拨。云雀谷的人手不够。\nQ我去。\nQ容我想想。",
        u"P\n先去天苍高地看看狼贼的先锋。\nQ这就去。"),
    10: (u"P\n海底神殿底下有座遗迹，近来夜里发青光。老朽走不动了，你去看看。\nQ我去探。\nQ改日。",
         u"P\n先去殿里找无面，他守了那遗迹三十年。\nQ这就去。"),
    11: (u"P\n古木神庙的林子里有股邪气，连树都枯了半边。你敢不敢跟贫道走一趟？\nQ敢。\nQ再说吧。",
         u"P\n先去绿湾林地找张猎户，他最先觉出不对。\nQ这就去。"),
    12: (u"P\n又是你。这回的事比上回大——天上那道裂口，你看见了吗？\nQ看见了。\nQ还没有。",
         u"P\n先找我的副手，他在清风村盯着那道裂口。\nQ这就去。"),
    13: (u"P\n天苍城以北，狼骑的马蹄声一夜比一夜近。你来得正好。\nQ我去看看。\nQ再等等。",
         u"P\n先去夜幕镇找刀狼大人，他知道狼骑的来路。\nQ这就去。"),
    14: (u"P\n碎梦岭的风里有股腥气。你要是真想追这条线，就别怕走得远。\nQ我不怕。\nQ再想想。",
         u"P\n先去岭上看看飞翼虎，它嗅得到那股腥气。\nQ这就去。"),
    15: (u"P\n四国的国王都欠老朽一句话。你替我把话带齐，这事就了了。\nQ我去跑。\nQ稍后。",
         u"P\n先去永泽城见南遥国王，剩下三位他会告诉你顺序。\nQ这就去。"),
    16: (u"P\n太阳神在沙海里留了句话，金绿沙洲的人听得见半句。你既然到了，就替我走完。\nQ我去。\nQ歇一歇。",
         u"P\n先去夜幕镇找沙漠行商，他认得进沙洲的路。\nQ这就去。"),
}

FINISH_TEXT = {
    7: u'P\n四国的事都了了。这份谢礼收着，别推。\nQ多谢金老板。',
    8: u'P\n兔族安顿下来了。这点心意，你拿着。\nQ多谢朱伯。',
    9: u'P\n北道通了。这条命是你替我捡回来的。\nQ分内之事。',
    10: u'P\n遗迹的门关严了。老朽这一桩心事，总算了了。\nQ老人家保重。',
    11: u'P\n邪气散尽，林子能活回来。这卷图你收着。\nQ多谢道长。',
    12: u'P\n裂口合上了。这颗果子是天上掉下来的，给你。\nQ多谢。',
    13: u'P\n狼骑退到三十里外了。这趟差事，办得漂亮。\nQ应该的。',
    14: u'P\n线到这里就断了，可你学到的东西断不了。\nQ多谢战锋。',
    15: u'P\n四句话都带到了。这副鞍，老朽替你要来的。\nQ多谢老人家。',
    16: u'P\n神谕听全了。这份东西，本该给走得最远的人。\nQ多谢教头。',
}

FINISH_MAGIC = {7, 8, 10, 12, 14}     # 资料写明「领悟招式1个」的收尾步
BASE_COINS = {7: 200, 8: 250, 9: 300, 10: 350, 11: 400,
              12: 450, 13: 500, 14: 550, 15: 600, 16: 650}
BASE_EXP = {7: 300, 8: 380, 9: 460, 10: 540, 11: 620,
            12: 700, 13: 780, 14: 860, 15: 940, 16: 1020}


# ── 小工具 ────────────────────────────────────────────────────────────────
def _qty_of(m):
    v = m.group(1)
    return int(v) if v.isdigit() else CN_NUM.get(v, 1)


def split_qty(seg):
    """'高级魔兽要诀2本' → ('高级魔兽要诀', 2)；'1级宝石包一个' → ('1级宝石包', 1)。"""
    seg = seg.strip()
    m = QTY_RE.search(seg)
    if m:
        name = seg[:m.start()].strip()
        if name:
            return (name, _qty_of(m))
    return (seg, 1)


def parse_rewards(inner):
    """从 token 括号内容拆出 (物品列表[(名, 数量)], 剧情点, 是否领悟招式)。"""
    items, point, learn = [], 0, False
    m = POINT_RE.search(inner)
    if m:
        point = int(m.group(1))
    if u'领悟招式' in inner or u'领悟主修' in inner:
        learn = True
    m = REWARD_RE.search(inner)
    if m:
        for seg in re.split(u'[；;]', m.group(1)):
            seg = seg.strip()
            if not seg or seg.startswith(u'若已领悟'):
                continue
            if re.match(u'^剧情点\\d+点$', seg):
                continue
            if any(k in seg for k in IGNORE_REWARD):
                continue
            if u'或' in seg or u'和' in seg:
                seg = seg.replace(u'随机获得', u'').replace(u'一套', u'')
                for part in re.split(u'[和或]', seg):
                    if part.strip():
                        items.append(split_qty(part))
                continue
            items.append(split_qty(seg))
    return items, point, learn


def resolve_map(docx_name):
    nm = ALIAS.get(docx_name, docx_name)
    return MAP_BY_NAME[nm], nm, (nm != docx_name)


def build():
    flows = json.load(io.open(os.path.join(TMP, 'quest_flows.json'),
                              encoding='utf-8'))
    flows = dict((q['no'], q) for q in flows)
    main = json.load(io.open(
        os.path.join(MX, '协议资料', '网上资料_20261022', 'quests_mainline.json'),
        encoding='utf-8'))
    main = dict((r['no'], r) for r in main['quests'])

    # 世界 NPC 索引：名字 → [(mapid, shape, (x, y))]
    npcpos = {}
    for mapid, rows in load_npcpos().items():
        for r in rows:
            nm = (r[0].decode('gbk', 'replace') if isinstance(r[0], bytes)
                  else str(r[0]))
            npcpos.setdefault(nm, []).append((mapid, r[2], r[4]))

    out, problems = [], []
    for no in range(7, 17):
        q = flows[no]
        tab = dict((r['npc'], (r['map'], r['x'], r['y'])) for r in q['npc_table'])
        toks = q['flow_tokens']
        steps = []
        for i, tok in enumerate(toks):
            if u'）注：' in tok:                   # 15 末步括号后挂着「注：…」
                tok = tok[:tok.index(u'）') + 1]
            m = re.match(u'^(.+?)（(.+)）$', tok)
            if m:
                name, inner = m.group(1).strip(), m.group(2)
            elif tok == u'完成':
                name, inner = None, u'完成'
            else:
                name, inner = tok.strip(), u''
            name = NAME_ALIAS.get(name, name)
            if name and name not in tab:
                problems.append((no, i, u'坐标表缺 NPC：%s' % name))

            fm = FIGHT_RE.search(inner)
            im = ITEM_RE.search(inner)
            need = split_qty(im.group(1)) if im else None
            rw, point, learn = parse_rewards(inner)

            if name is None or i == len(toks) - 1:
                kind = 'finish'
            elif fm:
                kind = 'fight'
            elif need:
                kind = 'item'
            else:
                kind = 'talk'
            if kind == 'finish' and name is None:
                name = main[no]['npc']
                if name not in tab:
                    problems.append((no, i, u'finish 触发 NPC %s 不在坐标表' % name))

            step = {'idx': i, 'kind': kind, 'npc': name,
                    'battle': fm.group(0) if fm else u'',
                    'need': need, 'items': rw, 'point': point,
                    'learn': learn}
            if kind == 'item':
                step['item'], step['qty'] = need
                if need[0] in ITEM_MISSING:
                    # 本版 itemdata 无此交付物 → 降级为对话，不造假 id、也不卡死流程
                    step['kind'] = 'talk'
                    step['note'] = (u'资料要求交付「%s」×%d，本版 itemdata 查无'
                                    u'此物，改为对话通过' % (need[0], need[1]))
            steps.append(step)

        # 摆位解析
        for st in steps:
            dmap, dx, dy = tab[st['npc']]
            mapid, mname, aliased = resolve_map(dmap)
            src = 'quest-spawn'
            npcid = shape = None
            for (mid, sh, pos) in npcpos.get(st['npc']) or ():
                if mid == mapid and pos:
                    src, npcid, shape, dx, dy = 'npcposdata', sh, sh, pos[0], pos[1]
                    break
            if src == 'quest-spawn':
                shape, sname = SHAPE.get(st['npc'], (4347, u'王捕头'))
                st['shape_name'] = sname
            st.update({'map': mapid, 'map_name': mname, 'x': dx, 'y': dy,
                       'src': src, 'npcid': npcid, 'shape': shape,
                       'docx_map': dmap, 'aliased': aliased,
                       'docx_pos': (tab[st['npc']][1], tab[st['npc']][2])})

        out.append({'no': no, 'name': q['name'],
                    'level': int(re.search(u'\\d+',
                                           main[no]['require']).group(0)),
                    'point': main[no]['story_point'],
                    'start_npc': main[no]['npc'], 'tab': tab, 'steps': steps})
    return out, problems, npcpos


# ── 台词 ──────────────────────────────────────────────────────────────────
BATTLE_TALK = {u'单挑战斗': u'只能你一个人上', u'组队战斗': u'对方人多，得组队',
               u'单人战斗': u'这仗只能一个人打', u'必败战斗': u'这一仗赢不了',
               u'两场战斗': u'要连着打两场'}

TALK_NEXT = (
    u'P\n去%s找%s（%d,%d），他知道后面的事。\nQ我去。',
    u'P\n这一环在%s——找%s（%d,%d）。\nQ我去。',
    u'P\n%s在%s（%d,%d）等着，你去说一声。\nQ我去。',
    u'P\n剩下的话该%s跟你讲，他在%s（%d,%d）。\nQ我去。',
)
TALK_FIGHT = (
    u'P\n%s在%s（%d,%d）作乱，去把它料理了。\nQ我去。',
    u'P\n去%s（%d,%d）把%s解决了。\nQ我去。',
    u'P\n%s堵在%s（%d,%d），拿下它。\nQ我去。',
)
TALK_ITEM = (
    u'P\n%s的%s（%d,%d）要%s×%d，你给他送去。\nQ我去。',
    u'P\n先给%s（%s %d,%d）备齐%s×%d。\nQ我去。',
    u'P\n%s在%s（%d,%d）等%s×%d，别让他久等。\nQ我去。',
)
FIGHT_TEXT = (
    u'P\n%s拦住去路（%s %d,%d）——%s。\nQ动手！\nQ先退开。',
    u'P\n%s就在%s（%d,%d）。%s，别留手。\nQ动手！\nQ先退开。',
    u'P\n%s挡在道上（%s %d,%d）。%s。\nQ动手！\nQ先退开。',
)
ITEM_TEXT = (
    u'P\n把%s×%d拿来，我就把知道的都告诉你。\nQ交出%s。\nQ我再去准备。',
    u'P\n空手来的？我要%s×%d，一件都不能少。\nQ交出%s。\nQ我再去准备。',
    u'P\n%s×%d——凑齐了再来说话。\nQ交出%s。\nQ我再去准备。',
)


def step_text(no, i, steps):
    st = steps[i]
    nxt = steps[i + 1] if i + 1 < len(steps) else None
    if st['kind'] == 'fight':
        return FIGHT_TEXT[i % 3] % (st['npc'], st['map_name'], st['x'],
                                    st['y'],
                                    BATTLE_TALK.get(st['battle'], u'只能动手'))
    if st['kind'] == 'item':
        return ITEM_TEXT[i % 3] % (st['item'], st['qty'], st['item'])
    if st['kind'] == 'finish':
        return FINISH_TEXT[no]
    if nxt is None:
        return u'P\n你先歇口气，剩下的事我慢慢说。\nQ好。'
    if nxt['npc'] == st['npc']:
        return u'P\n还有一桩，就在这儿说清楚。\nQ你说。'
    if nxt['kind'] == 'fight':
        v = TALK_FIGHT[i % 3]
        if u'把%s解决' in v:               # 第 2 式：地图在前、人名在后
            return v % (nxt['map_name'], nxt['x'], nxt['y'], nxt['npc'])
        return v % (nxt['npc'], nxt['map_name'], nxt['x'], nxt['y'])
    if nxt['kind'] == 'item':
        v = TALK_ITEM[i % 3]
        if u'先给' in v:                    # 第 2 式：NPC（地图 x,y）
            return v % (nxt['npc'], nxt['map_name'], nxt['x'], nxt['y'],
                        nxt['item'], nxt['qty'])
        if u'等%s' in v:                    # 第 3 式
            return v % (nxt['npc'], nxt['map_name'], nxt['x'], nxt['y'],
                        nxt['item'], nxt['qty'])
        return v % (nxt['map_name'], nxt['npc'], nxt['x'], nxt['y'],
                    nxt['item'], nxt['qty'])
    v = TALK_NEXT[i % 4]
    if u'剩下的话' in v or u'等着' in v:      # 第 3、4 式：人名在前
        return v % (nxt['npc'], nxt['map_name'], nxt['x'], nxt['y'])
    return v % (nxt['map_name'], nxt['npc'], nxt['x'], nxt['y'])


def step_desc(st):
    if st['kind'] == 'fight':
        return u'击败%s的%s（%d,%d）' % (st['map_name'], st['npc'],
                                       st['x'], st['y'])
    if st['kind'] == 'item':
        return u'带 %s×%d 给%s的%s' % (st['item'], st['qty'],
                                    st['map_name'], st['npc'])
    if st['kind'] == 'finish':
        return u'回%s向%s复命' % (st['map_name'], st['npc'])
    return u'去%s找%s' % (st['map_name'], st['npc'])


def qstr(s):
    """Python 字面量（转义反斜杠、引号、换行）。"""
    s = (s.replace(u'\\', u'\\\\').replace(u"'", u"\\'")
          .replace(u'\n', u'\\n').replace(u'\r', u'\\r').replace(u'\t', u'\\t'))
    return u"'%s'" % s


# ── 代码生成 ──────────────────────────────────────────────────────────────
def emit(quests):
    lines = []
    add = lines.append

    # ① NPC 摆位表
    add(u'    # ══════════════ 7–16：NPC 摆位（2026-10-22）══════════════')
    add(u'    # 出处：npcposdata = 客户端世界 NPC 真值（坐标与造型同源）；')
    add(u'    #       quest-spawn = 资料流程坐标表 + 模拟造型（客户端无此 NPC 实体）。')
    spawns = []
    seen = {}
    next_id = 8143
    for q in quests:
        for st in q['steps']:
            key = (st['map'], st['npc'])
            if key in seen:
                continue
            if st['src'] == 'npcposdata':
                seen[key] = st['npcid']
                spawns.append((st['map'], st['npcid'], st['shape'], st['x'],
                               st['y'], st['npc'], 'npcposdata'))
            else:
                seen[key] = next_id
                spawns.append((st['map'], next_id, st['shape'], st['x'],
                               st['y'], st['npc'],
                               'quest-spawn shape=SIM(%s)' % st['shape_name']))
                next_id += 1
    for (mid, nid, sh, x, y, nm, src) in spawns:
        add(u'    (%d, %d, %d, %d, %d, %s, %s),' %
            (mid, nid, sh, x, y, qstr(nm), qstr(src)))
    add(u'')
    return u'\n'.join(lines), spawns, seen


QID_BASE = 100          # qid = 100 + no（1–6 用 101–106，7–16 → 107–116）
qid_of = lambda no: QID_BASE + no
LATER_PREV_NO = {15: (10, 11, 12, 13, 14)}   # 资料「完成剧情10、11、12、13、14」


def emit_quests(quests):
    """生成 QUESTS 里 7–16 的条目源码。"""
    out = []
    add = out.append
    add(u'    # ══════════════ 7–16：飞升前主线（2026-10-22）══════════════')
    add(u'    # 流程与 NPC 坐标来自《梦想世界2013资料全库》逐条「流程」+ 坐标表；')
    add(u'    # 等级/剧情点/触发 NPC/场景来自一览表（quests_mainline.json）。')
    add(u'    # 资料的流程首节点就是「找谁接」，故并进 `accept` 台词，steps 从第 2 节点起。')
    for q in quests:
        no = q['no']
        steps = q['steps'][1:]          # 去掉「找触发 NPC 接任务」这一节点
        qid_name = 'Q_%02d' % no
        add(u'    %s: {' % qid_name)
        add(u"        'qid': %s," % qid_name)
        add(u"        'no': %d," % no)
        add(u"        'name': %s," % qstr(q['name']))
        add(u"        'level': %d," % q['level'])
        add(u"        'country': None,")
        add(u"        'point': %d," % q['point'])
        if no == 15:
            prev_q = tuple(QID_BASE + n for n in LATER_PREV_NO[15])
            add(u"        'prev': %s," % (u'(%s)' % u', '.join(
                str(x) for x in prev_q)))
        else:
            add(u"        'prev': (),")
        add(u"        'start_map': %d," % q['steps'][0]['map'])
        add(u"        'start_npc': %s," % qstr(q['start_npc']))
        intro, accept = INTRO[no]
        add(u"        'intro': %s," % qstr(intro))
        add(u"        'accept': %s," % qstr(accept))
        add(u"        'steps': [")
        for i, st in enumerate(steps):
            add(_step_src(no, i, steps, st))
        add(u'        ],')
        add(u'    },')
        add(u'')
    return u'\n'.join(out)


def _step_src(no, i, steps, st):
    """单个步骤的源码（多行，按字段分行便于阅读）。"""
    parts = [u"            {'kind': %s," % qstr(st['kind']),
             u"             'map': %d," % st['map'],
             u"             'npc': %s," % qstr(st['npc'])]
    if st['kind'] == 'fight':
        parts.append(u"             'x': %d, 'y': %d," % (st['x'], st['y']))
        parts.append(u"             'monster': %s, 'shape': %d, 'shape_sim': True,"
                     % (qstr(st['shape_name']), st['shape']))
    elif st['kind'] == 'item':
        parts.append(u"             'item': %s, 'qty': %d,"
                     % (qstr(st['item']), st['qty']))
    if st.get('note'):
        parts.append(u"             'note': %s," % qstr(st['note']))
    parts.append(u"             'desc': %s," % qstr(step_desc(st)))
    parts.append(u"             'text': %s," % qstr(step_text(no, i, steps)))
    rw = _reward_src(no, st)
    if rw:
        parts.append(u"             'reward': %s," % rw)
    parts[-1] = parts[-1][:-1]          # 末字段去掉尾逗号，便于收尾
    return u'\n'.join(parts) + u'},'


def _reward_src(no, st):
    bits = []
    if st['kind'] == 'fight':
        bits.append(u"'coins': %d" % BASE_COINS[no])
        bits.append(u"'exp': %d" % BASE_EXP[no])
    elif st['kind'] == 'finish':
        bits.append(u"'coins': %d" % (BASE_COINS[no] * 3))
        bits.append(u"'exp': %d" % int(BASE_EXP[no] * 1.5))
    if st['point']:
        bits.append(u"'point': %d" % st['point'])
    if st['kind'] == 'finish' and no in FINISH_MAGIC:
        bits.append(u"'learn_magic': True")
    if st['items']:
        its = []
        for (nm, qt) in st['items']:
            shape, cname, note = REWARD[nm]
            if shape is None:
                its.append(u"{'name': %s, 'shape': None, 'qty': %d, 'note': %s}"
                           % (qstr(cname), qt, qstr(note)))
            else:
                its.append(u"{'name': %s, 'shape': %d, 'qty': %d, 'note': %s}"
                           % (qstr(cname), shape, qt, qstr(note)))
        bits.append(u"'items': [%s]" % (u',\n                        ').join(its))
    if not bits:
        return ''
    return u'{%s}' % u', '.join(bits)


def main():
    quests, problems, npcpos = build()
    json.dump([{'no': q['no'], 'name': q['name'], 'level': q['level'],
                'point': q['point'], 'start_npc': q['start_npc'],
                'steps': [dict((k, v) for k, v in st.items()
                               if k != 'docx_pos') for st in q['steps']]}
               for q in quests],
              io.open(os.path.join(TMP, 'quest_716_steps.json'), 'w',
                      encoding='utf-8'), ensure_ascii=False, indent=1)

    bad_sum = [(q['no'], sum(st['point'] for st in q['steps']), q['point'])
               for q in quests
               if sum(st['point'] for st in q['steps']) != q['point']]
    miss_item, miss_reward, miss_shape = set(), set(), set()
    for q in quests:
        for st in q['steps']:
            if st['need'] and st['need'][0] not in ITEM \
                    and st['need'][0] not in ITEM_MISSING:
                miss_item.add(st['need'][0])
            for nm, _qt in st['items']:
                if nm not in REWARD:
                    miss_reward.add(nm)
            if st['src'] == 'quest-spawn' and st['npc'] not in SHAPE:
                miss_shape.add(st['npc'])

    print('== 剧情点合计 ==', bad_sum or u'全部吻合')
    print('== 未登记物品 ==', sorted(miss_item) or u'无')
    print('== 未登记奖励 ==', sorted(miss_reward) or u'无')
    print('== 未定造型 NPC ==', sorted(miss_shape) or u'无')
    print('== 解析告警 ==', problems or u'无')
    src, spawns, seen = emit(quests)
    print('== 摆位 %d 条（世界 %d / 剧情 %d）==' % (
        len(spawns),
        sum(1 for s in spawns if s[6] == 'npcposdata'),
        sum(1 for s in spawns if s[6] != 'npcposdata')))
    print('== 步骤合计 %d（资料节点 %d，首节点并入 accept）==' % (
        sum(len(q['steps']) - 1 for q in quests),
        sum(len(q['steps']) for q in quests)))
    io.open(os.path.join(MX, '_quest_716_spawns.txt'), 'w',
            encoding='utf-8').write(src)
    qsrc = emit_quests(quests)
    io.open(os.path.join(MX, '_quest_716_quests.txt'), 'w',
            encoding='utf-8').write(qsrc)

    # 逐条打印步数与剧情点，供人工核对
    for q in quests:
        sts = q['steps'][1:]
        print('  %2d %-6s qid=%d Lv%-2d 剧情点%d 步数%2d 战斗%2d 交物%2d' % (
            q['no'], q['name'], qid_of(q['no']), q['level'], q['point'],
            len(sts), sum(1 for s in sts if s['kind'] == 'fight'),
            sum(1 for s in sts if s['kind'] == 'item')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
