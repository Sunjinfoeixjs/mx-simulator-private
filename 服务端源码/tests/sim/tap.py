# -*- coding: utf-8 -*-
"""协议抓包与解码核心 —— 可视化调试台的数据层。

为什么是「包装 conn」而不是「插桩 sendall」
------------------------------------------
`m4_server.py` 的收发没有收口点：C2S 只有 `recv_c2s()` 一处，S2C 却有约 140 处裸
`conn.sendall(...)`。逐点插桩既危险又一定会漏。内核拿到的 `conn` 始终只是
`sendall / recv / settimeout / close` 四件套（见《单机游戏架构》§三），所以把 conn
换成 `TappedConn` 就拿到了**全部**上下行字节，而 `m4_server.py` 一行不改。

字节流在本模块内按真实帧格式重新分帧（协议分析器的标准做法，比在调用点记录更
权威——连「一次逻辑帧拆成多次 sendall」也照样还原）：

    S2C：[1B N][opcode 1B][data]        N 含 opcode；N==0xFF → 0xFF + u16
    C2S：[opcode 1B][1B N][body]        N 不含 opcode；N==0xFF → 0xFF + u16

解码的三层来源，按优先级
------------------------
1. **精确布局覆盖表** `OVERLAY`（本文件内）—— 逐字段对齐 `m4_server.build_*`，
   这是单机会话里**真正看得见**的那些帧，字段名直接取自源码注释里的客户端读音。
2. **字典 C2S 字段表** `协议资料/字典/protocol_functions.json`（`dir=="C2S"` 自带
   opcode）。同一 opcode 常有多种调用形态，解码器会**逐种试配**，取最贴合 payload 的。
3. **字典 S2C 桥接**：该字典的 S2C 条目**不带** opcode，用客户端
   `客户端源码/src/net.py` 的 `NET_FUNCTION_NAME`（opcode → 模块.处理函数）桥接。
   实测 168 条路由 100% 命中字段表，但要注意字典里部分条目是「分发器展开」形态
   （`args == 'netobj'`），字段数并不等于线上布局——这类偏差不隐藏，见下。

偏差一律显式暴露
----------------
解码器只按定义长度推进游标，多出来的字节进 `tail`，定义之外/越界时给 `note`；
多种布局试配的结果取「恰好吃满 payload」的那一种，并在 `note` 里写明用的是第几种。
协议还原工作本身就需要看见这些偏差，而不是被一个漂亮但错的解析骗过去。

对外接口
--------
    tap = FrameTap()
    tap.install(m4_server)              # monkey-patch handle/log，零侵入、幂等
    tap.frames(since=0)                 # 增量取帧（可视化刷新）
    tap.stats()                         # 协议覆盖统计（= 待完善清单）
    tap.inject('S2C', 152, payload)     # 下行注入：测客户端对某帧的反应
    tap.inject('C2S', 98, b'\\x00')      # 上行注入：免点 GUI 复现一次操作
    tap.drop_next('S2C')                # 吞掉下一帧（测缺帧 / 超时容错）
    tap.export_jsonl(path)              # 会话落地，可回放、可进版本库

仅使用标准库。
"""
import io
import json
import os
import re
import socket
import struct
import threading
import time

import lzss as _lzss


def _maybe_lzss(payload):
    """S2C 帧体的 LZSS 透明解压（原服加密通道：S2C 先压缩再走 XOR 流）。

    加密模式下 make_s2c 会先 LZSS 压缩再封帧（长度前缀=压缩后长度），Hello 三帧
    则显式不压缩。抓包器只知道「帧体可能是压缩的」，用**往返校验**做确定性判别：
    解压成功的流用同一压缩器重压必得原字节（压缩器确定性、LZSS 无损）；
    非压缩帧解压出的是废码，round-trip 几乎不可能等于原帧 —— 原样返回。
    失败/不成立一律不动原帧，抓包原则「不隐藏偏差」保持不变。
    """
    if not payload:
        return payload
    try:
        raw = _lzss.decompress(payload)
        if raw and _lzss.compress(raw) == payload:
            return raw
    except Exception:                          # noqa: BLE001 —— 判别失败就是非压缩帧
        pass
    return payload

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_mx_root(start):
    """向上找到工程根（含「协议资料」目录的那层）。

    ★ 原实现写死 `join(HERE,'..','..')`：只在「sim 与 tests 平级」的旧布局成立。
      本工作区 sim 在 服务端源码/tests/sim 里，`..\..` 落到 服务端源码（无协议资料），
      于是字典/ net.py 全部 FileNotFoundError（调试台只剩 hex 视图）。
    """
    d = os.path.normpath(start)
    for _ in range(6):
        if os.path.isdir(os.path.join(d, '协议资料')):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return os.path.normpath(os.path.join(start, '..', '..'))


MX_ROOT = _find_mx_root(HERE)

DICT_PATH = os.path.join(MX_ROOT, '协议资料', '字典', 'protocol_functions.json')
NET_PY_PATH = os.path.join(MX_ROOT, '客户端源码', 'src', 'net.py')

RING_MAX = 6000          # 环形缓冲上限：可视化只关心「最近发生了什么」
_READ_SLICE = 0.1        # TappedConn.recv 分片读底层的秒数（片间检查注入队列）


# ═══════════════════════════════════════════════ 精确布局覆盖表 ══
# 字段规格 DSL（空格分隔的 `名字:类型`）：
#   2 / 1 / 4 无符号整数（字节宽度）    1s / 2s / 4s 有符号整数
#   s         变长 C 字符串（GBK + \0）  v  1B 长度前缀的变长字符串
#   f16/f20/f26 定长字符串（定长宽度）    T  吃掉剩余全部字节  132r 定长原始块
#   D         「外观描述」标签联合（客户端 prop.UnpackDesc）
#   P(字段名) 定长字符串，宽度由**前面某个字段的值**给出（如 title_len 后的 title）
#
# 来源：m4_server.build_* 的 docstring 与 struct.pack 实参，逐字段对齐。
# 这些是单机会话里真正会出现在抓包里的 S2C 帧。
_OV_S2C = {
    1: ('S2CHello', 'seed:4 servernum:2'),
    2: ('S2CLoginAccountInfo', 'count:1 now:4 vid:1'),
    3: ('S2CLoginRoleInfo', 'hero_id:4 f1:4 f2:1 f3:2 f4:2 f5:1 f6:1 f7:1 f8:4 name:f16'),
    15: ('mxgw.S2CTime', 'ticks:4'),
    16: ('S2CPropInit', 'hero_id:4 icon:1 shape:2 desc:D sex:1 name:f20 grade:1 '
                        'cash:8 saving:4 exp:4 nextexp:4 needexp:4 cls:1 country:1 '
                        'org:f20 emote:1 title:f22 '
                        'p16:2 p17:2 p18:2 p19:2 p20:2 p21:2 p22:2 p23:2 p24:2 p25:2 p26:2 p27:2 '
                        'p28:2 p29:2 p30:2 p31:2 p32:2 p33:2 p34:2 p35:2 p36:2 p37:2 '
                        'p38:4 p39:1 p40:4 p41:4 p42:2 p43:4 p44:4 p45:4 p46:4 p47:4 p48:4 '
                        'p49:4 p50:4 p51:4 p52:4 p53:1 p54:1 p55:1 p56:1 p57:4 p58:1 p59:1 '
                        'p60:1 p61:2 p62:1 p63:4 p64:4 p65:2 p66:2'),
    # op17 的 value 宽度随 type 变，静态规格只是「无 payload 时」的占位；
    # 真实解码走 _OV_DYN[17]（见 _dyn_prop_change）。
    17: ('S2CPropChange', 'type:1 value:4'),
    18: ('S2CPropResist', 'm_Resist_Xian:1s m_Resist_Gui:1s m_Resist_Yao:1s '
                          'm_Resist_Zhi:1s m_Resist_Nian:1s'),
    19: ('S2CPropPower', 'm_At:1 m_Df:1 m_Dp:1'),
    # nethero 高频补全（M9 第二批）
    22: ('S2CPropRepute', 'rid:1 lv:1s val:4s maxv:4 name:s'),
    25: ('S2CPlayerCash', 'cash:4 cash2:4'),
    26: ('S2CPropAdvXinfa', 'n:1 ids:L{count:n:v:2}'),
    32: ('S2CItemKey', 'id:4 key:2'),
    33: ('S2CItemAdd', 'type:1 id:4 shape:4 special_flag:2 quantity:1 pos:1'),
    34: ('S2CItemDel', 'type:1 id:4'),
    35: ('S2CItemQuantity', 'id:4 quantity:1 flash:1'),
    36: ('S2CItemPos', 'type:1 id:4 pos:1'),
    38: ('S2CItemString', 'id:4 infoext:s'),
    48: ('S2CSummonInfo', 'pid:4 name:f20 shape:2 desc:D grade:1 hp:4 mp:4 fp:4 '
                          'hpmax:4 mpmax:4 fpmax:4 damage:4 defense:4 speed:4 magdef:4 '
                          'str:2 con:2 mag:2 cha:2 dex:2 point:2 exp:4 expnext:4 '
                          'expneed:4 life:4 molingguo:1'),
    49: ('S2CSummonSkill', 'pid:4 n:1 hf_from:1 skill_ids:T'),
    50: ('S2CSetFight', 'pid:4'),
    51: ('S2CPropView', 'pid:4 extra_zz:2 atk:2 def:2 con:2 magic:2 speed:2 '
                        'update_flag:4 update_max:4 promote_zz:1 promote_max:2 '
                        'resist_lidi:1s resist_lingqiao:1s resist_qungong:1s '
                        'resist_putong:1s resist_xian:1s resist_gui:1s '
                        'resist_yao:1s resist_zhi:1s resist_nian:1s'),
    53: ('S2CSummonKey', 'pid:4 key:2 birthkey:1'),
    # 召唤兽装备（netpet.S2CSummEquip，2026-10-23 P1）：pid + 循环
    #   [pos(1) shape(4) desc_len(1) desc(n)]，pos<=0 结束（哨兵即终止位）。
    # 客户端 dlgpetitem.DrawPetEquip 只认 pos ∈ {1,2,3}（项圈/铠甲/护符）。
    54: ('S2CSummEquip', 'pid:4 equips:L{sentinel:pos:pos:1 shape:4 desc:v}'),
    65: ('S2CSkillLevel', 'sktype:1 skid:2 sklevel:1 其余:Tr'),
    72: ('S2CTaskAdd', 'tid:2 icon:1 name:f26 desc:v detail_len:2 npc_cnt:1 oid_cnt:1 '
                       'timelimit:4 tasktype:4 flag:1 t:1 tipss:s'),
    73: ('S2CTaskRemove', 'tid:2'),
    84: ('S2CGroup', 'sub:1 body:T'),
    # netteam：组队（op82 下行）。sub 决定 body 结构，见 team.py 的 S_* 常量：
    #   1 TeamStatus / 2 TeamList / 3 RequestJoin / 4 AnswerJoin / 5 RequestList /
    #   6 TeamInfo / 7 GPS / 8 InviteReturn / 10 SearchInfo / 11 TeamerInfo / 12 TeamTarget
    # 本服只主动发 1/2/6/7/11/12（其余 sub 需要真人对手方）。TeamList 每人 17 字段、
    # TeamInfo 尾接变长串，静态拆不彻底，尾部留 T（原始字节）由调试台自行展开。
    82: ('S2CPlayerTeam', 'sub:1 body:T'),
    # netmap：场景内对象增删 / 变身 / 特效（进图后最高频的一批）
    # ★ desc 是 UnpackDesc 标签联合，不是 C 字符串——写 `desc:s` 只在 desc 为空时成立。
    85: ('S2CMapDel', 'id:4'),
    86: ('S2CMapTrack', 'obj_id:4 x:2 y:2 dx:1s dy:1s move_type:1'),
    87: ('S2CProduce', 'sub:1 iOpen:1 iscene:4 n:1 entries:T'),
    89: ('S2COpenUI', 'sub:1 body:T'),
    96: ('S2CMapSceneM', 'scene:2 mapid:2 minimap:2 x:2 y:2 name:s'),
    97: ('S2CMapGotoM', 'x:2 y:2'),
    98: ('S2CMapAddPlayer', 'id:4 shape:2 x:2 y:2 desc:D country:1 hideflag:1 '
                           'emotetype:1 s:1 playername:P(s) emotefortitle:1 title:f22 color:1 update_flag:1'),
    99: ('S2CMapAddNpc', 'id:4 shape:2 x:2 y:2 block:1 desc:D direct:1 npctype:2 '
                         'country:1 addonId:2 title_len:1 title:P(title_len) name:s'),
    100: ('S2CMapAddGoods', 'id:4 shape:2 x:2 y:2 color:1 state:1 name:s'),
    101: ('S2CMapRefreshNpcSign', 'sub:1 npcId:4 taskId:4 type:1 color:1'),
    102: ('S2CMapEffect', 'mode:1 tid:4 eid:2'),
    # 105 S2CMapSummon：随行宠物（进世界/切图必发）。逐条结构见 _OV_NOTE[105]；
    #   这里 entries 用 T 吃剩余字节（decode_fields 无循环构造，与 147/148/149 同约定）。
    105: ('S2CMapSummon', 'owner_id:4 flag:1 n:1 entries:L{count:n:style:1 shape:2 desc:D name:v title:v}'),
    106: ('S2CChangeShape', 'id:4 shape:2 desc:D country:1 emotetype:1 '
                            'emotefortitle:1 title:f22 color:1'),
    108: ('S2CMapHide', 'pid:4'),
    # ★ 112–129（战斗帧）不在此手写：字段定义统一在 `frames.SPEC`，
    #   由文件末尾 `OVERLAY.update(frames.tap_specs())` 注入。
    #   历史教训：这三份定义（build_war_* / 分派解析 / 本表）各自漂移过——
    #   166 条件字段、169 缺 id、115/123 位序都吃过亏，所以只留一份。
    # 58 S2CMobile：手机安全面板回读（dialog/dlgsysopt，2026-10-22）。
    #   静态只能确认 sub(1)——客户端读完 sub 就按子命令分派，后续字段
    #   （check/receive/enable_friends/limit/degree/money/balance）在分派函数里读，
    #   静态提取跟不进去，因此走 `_OV_DYN` 按 sub 展开。
    58: ('S2CMobile', 'sub:1'),
    147: ('S2CNpcBuyList', 'mode:1 ticketid:4 ext:1 items:L{sentinel:shape:shape:4 num:2 price:4}'),
    148: ('S2CNpcSellList', 'mode:1 items:L{sentinel:shape:shape:4 amount:2 price:4}'),
    149: ('S2CNpcLearnList', 'mode:1 icost:2 entries:L{sentinel:skid:skid:2 npcLimit:1 validLearn:1 freeLevel:1}'),
    151: ('S2CNpcDetails', 'pos:1 desc:s'),
    152: ('S2CNpcChatExt', 'ranKey:2 npctype:4 name:f20 msg:s'),
    155: ('S2CNpcMakeCost', 'iTili:2 iMoney:2 sklv:2'),
    160: ('S2CMakeCompleteNotify', 'shape:4 id:4 amount:1 desc:s'),
    # ★ op148/149：items/entries 用 T（吃剩余字节）原始块展示，与 op147 同约定；
    #   真实循环结构见 _OV_NOTE 147/148/149，解码器不循环（decode_fields 无循环构造）。
    # nethero / netmap 其余高频帧（M9 第二批）
    162: ('S2CItemInfo', 'itype:1 key:2 id:4 ext:s'),
    164: ('S2CNotifyExt', 'itype:1 shape:4 msg:s'),
    171: ('S2CAddTempNpc', 'id:4 shape:2 name:s'),
    179: ('S2CServiceAnswer', 'sub:1 flag:1 answer:s'),
    190: ('S2CItemTCInfo', 'itype:1 key:2 id:4 ext:s'),
    192: ('dlgcalendar.S2CTime', 'unix_sec:4'),
}

# ★ 战斗帧字段定义从 `frames.SPEC` 单一来源注入（112–129）。
#   以前这里是同一套字段的第三份拷贝，与 build_war_* / 分派解析各自漂移。
try:
    import frames as _frames
    _OV_S2C.update(_frames.tap_specs())
except Exception as _e:                      # noqa: BLE001 —— 调试台不该因此起不来
    print('⚠ tap: frames.tap_specs() 不可用（%r），战斗帧将回退到字典解码' % (_e,))

# 覆盖表解码时补充的语义提示（不塞进规格串，免得 DSL 太吵）
_OV_NOTE = {
    48: 'desc 是 UnpackDesc 标签联合（不是 C 字符串），与 op98/99/106 同；空 desc 时 n=0（单字节 0x00），与 C 串恰巧重合',
    22: 'lv/val 有符号（UnpackSigned）；name 为 C 字符串',
    115: 'bf 是 MSB-first 位域：allied=bit7 / type=bit6 / child=bit5；desc 是 UnpackDesc（不是 C 串）',
    85: '长度 8 → 8×1B 区块；否则 id(4B) 单个对象',
    98: 'desc 是 UnpackDesc 标签联合；country 后跟 hideflag(1B 隐身标志，非零时客户端把 shape 改成 9999 即删除该对象) 与 emotetype；name 由 s:1(1B 长度前缀) + playername:s(变长) 给出；title 定长 22',
    96: 'scene 首字段分派三种布局（_OV_DYN）：普通 = scene+mapid+minimap+x+y+name（本服只发这种）；65535 特殊场景 = mark+真实 scene+6 个场景参数+公共尾；65534 迷宫 = mark+scene+房间/道路 7×1B+n(2)+n 组不可通行边 [a(1) b(1)]（a<=0 提前结束）+公共尾',
    49: 'n 决定后续 skill_id(2) 的条数（_OV_DYN 动态展开成 sk0..sk(n-1)）；x 是护符起始位（本服 build_pet_skill 的 hf_from 参数），客户端护符技能数 = n - x',
    99: 'desc 是 UnpackDesc（不是 C 字符串）；title_len>0 时才跟 title',
    100: 'name 为 C 字符串（货物名）',
    101: 'sub 决定主体：1=图标(type/color) / 2=需求物品(itype/shape)',
    102: 'mode 决定主体：0=目标+特效 / 1-3=坐标+特效 / 4=飘血 / 5=坐标+…',
    106: 'desc 是 UnpackDesc；title 定长 22',
    164: 'itype=6 时是 4×4B 造型四元组，否则单个 4B',
    65: 'sktype 决定后续字段（_OV_DYN 八分支）：2=学招式(+bid/point/cash/iHighLv，本服 build_skill_level_magic)；7=装备进战斗菜单(到此结束，本服 build_skill_equip)；10=心法经验(exp/expnext，不读 skid/sklevel)',
    72: 'desc 为 1B 长度前缀；Tipss 为 C 字符串',
    123: '位域 MSB-first：bit7-4=hurt，bit3-2=life，bit1-0=ward；hp_chg 有符号',
    122: 'vic_ids 为 1B × N（N = 帧内剩余字节数）',
    3: 'f* 为服务端预留/未用字段，客户端当前不读',
    87: 'sub=5 时 body = iOpen(1) iscene(4) n(1) + [x(2) y(2) type(1)]×n',
    89: 'sub 决定 body 结构（如 sub182 小地图 NPC 标记）',
    84: 'sub 决定 body 结构（如 sub80 开帮派面板 / sub81 成员列表）',
    147: 'items 是原始块：循环 [shape(4) num(2) price(4)] 直到 shape==0 哨兵',
    148: 'items 是原始块：循环 [shape(4) amount(2) price(4)] 直到 shape==0 哨兵；mode 0 客户端不弹窗',
    149: 'entries 是原始块：循环 [skid(2) npcLimit(1) validLearn(1) freeLevel(1)] 直到 skid==0 哨兵；icost 对 1101-1109 段无意义填 0',
    151: 'desc 为 C 字符串；dlgtrade.UpdateDetails 把它画进交易面板提示框（应答 C2SNpcGetDetails 的面板格位悬停）',
    105: 'entries 用 L{count:n} 结构化展开（2026-10-20）：循环 n 次，每条 = style(1) shape(2) desc(UnpackDesc) '
         'namelen(1)+name titlelen(1)+title?；n 是前面那个计数字段。本服只发 n=1（随行宠物）。'
         '★ 名字/称号是「1B 长度前缀 + 裸 GBK」，与 op99 AddNpc 的 C 字符串（\\0 结尾、无前缀）不同，勿混。'
         '解出形如 [{style:0, shape:4005, desc:None, name:"梦想企鹅", title:""}]',
    129: 'id=被标记的己方战斗单位（本服宠物 250），op=0 表示放出/可指挥；'
         '客户端 netwar.S2CWarSummon 只读这两个字节，随后把该单位纳入召唤指令菜单',
}


def parse_spec(spec):
    """把字段规格串编译成 decode_fields 认得的字段定义列表。"""
    fields = []
    # ★ 先提取 L{...} 循环块（含空格，不能被普通 split 拆开）。
    #   把 L{...} 暂存、从 spec 里移除、split 后再在对应位置插回。
    # ★ 先提取 L{...} 循环块（含空格，不能被普通 split 拆开）。
    #   正则把 'name:L{...}' 整体作为一个 token 提取。
    import re as _re
    _pat = _re.compile(r'(\w+:L\{[^}]*\})')
    _merged = []
    _rest = spec or ''
    while _rest:
        _m = _pat.search(_rest)
        if _m:
            # _m 之前的普通 token
            _before = _rest[:_m.start()].strip()
            if _before:
                _merged.extend(_before.split())
            _merged.append(_m.group())
            _rest = _rest[_m.end():]
        else:
            _merged.extend(_rest.split())
            break
    for tok in _merged:
        if ':' not in tok:
            continue
        if tok.startswith('') and 'L{' in tok:
            # L{} 循环：name:L{sentinel:field:...} 或 name:L{count:ref:field:...}
            # rsplit 会按最后一个冒号拆，而 L{} 内有冒号 → 必须先提取。
            _lbr = tok.index('L{')
            _name = tok[:_lbr].rstrip(':')
            _inner = tok[_lbr + 2:-1]  # 去掉 L{ }
            if _inner.startswith('sentinel:'):
                _ctrl = 'sentinel:' + _inner[len('sentinel:'):].split(':')[0]
                _body = _inner[len('sentinel:') + _inner[len('sentinel:'):].index(':') + 1:]
            elif _inner.startswith('count:'):
                _ctrl = 'count:' + _inner[len('count:'):].split(':')[0]
                _body = _inner[len('count:') + _inner[len('count:'):].index(':') + 1:]
            else:
                _ctrl = _inner
                _body = ''
            _sub = parse_spec(_body.replace(',', ' '))
            fields.append({'kind': 'L', 'size': '0', 'value': _name,
                           'control': _ctrl.strip(), 'sub_fields': _sub})
            continue
        name, t = tok.rsplit(':', 1)
        if t == 's':
            fields.append({'kind': 'S', 'size': '0', 'value': name})
        elif t == 'v':
            fields.append({'kind': 'V', 'size': '0', 'value': name})
        elif t == 'T':
            fields.append({'kind': 'T', 'size': '0', 'value': name})
        elif t == 'Tr':
            fields.append({'kind': 'T', 'size': '0', 'value': name})
        elif t == 'D':
            fields.append({'kind': 'D', 'size': '0', 'value': name})
        elif t.startswith('B(') and t.endswith(')'):
            # 位域容器：一个字节里拆多个位，`bf:B(allied=7,type=6,child=5)`
            # ★ 位号就是二进制里的位号（bit7 = 0x80），与客户端 `UnpackBit(0,1)` 的
            #   off=0 对应 —— 即 off + 位号 == 7。写 7 而不是 0，是为了跟文档/口头说法一致。
            bits = []
            for part in t[2:-1].split(','):
                k, _sep, v = part.partition('=')
                if k.strip() and v.strip().isdigit():
                    bits.append((k.strip(), int(v.strip())))
            fields.append({'kind': 'B', 'size': '1', 'value': name, 'bits': bits})
        elif t.startswith('P(') and t.endswith(')'):
            # 宽度来自前面某个字段：`title:P(title_len)`
            fields.append({'kind': 'P', 'size': t[2:-1], 'value': name})
        elif t.startswith('f') and t[1:].isdigit():
            fields.append({'kind': 'S', 'size': t[1:], 'value': name})
        elif t.endswith('r') and t[:-1].isdigit():
            fields.append({'kind': 'R', 'size': t[:-1], 'value': name})
        elif t.endswith('sr') and t[:-2].isdigit():
            fields.append({'kind': 'R', 'size': t[:-2], 'value': name, 'signed': True})
        elif t.endswith('s') and t[:-1].isdigit():
            fields.append({'kind': 'I', 'size': t[:-1], 'value': name, 'signed': True})
        elif t.isdigit():
            fields.append({'kind': 'I', 'size': t, 'value': name})
        elif t.startswith('L{') and t.endswith('}'):
            # ★ 循环构造（2026-10-20）：L{sentinel:field,field,...} 或 L{count:field,field,...}
            #   sentinel: 循环直到某个字段的值 == 0（哨兵），如 147/148/149 的 items。
            #   count:   循环 N 次，N 来自前面某个已解出的计数字段，如 105 的 n。
            #   字段用逗号分隔，语法同普通字段（shape:4 num:2 price:4）。
            inner = t[2:-1]  # 去掉 L{ }
            ctrl, _, body = inner.partition(':')
            sub_fields = parse_spec(body.replace(',', ' '))
            fields.append({'kind': 'L', 'size': '0', 'value': name,
                           'control': ctrl.strip(),
                           'sub_fields': sub_fields})
    return fields


OVERLAY = {'S2C': {op: parse_spec(spec) for op, (_n, spec) in _OV_S2C.items()}}
OVERLAY_NAME = {'S2C': {op: name for op, (name, _s) in _OV_S2C.items()}}


# ══════════════════════════════════════════ 随帧内容变宽的布局 ══
# 绝大多数帧的布局是常量，一份静态规格就够。少数帧不是：op17 的 value 宽度取决于
# 首字节 type 指向哪个属性，静态规格只能写死一个宽度，**写死哪个都错**。
#
# 下标 → 属性名/宽度，来源是客户端 `src/hero.py:ITEM_ALL`（75 项，下标 0..74）。
# 宽度取自各项 `m_Type` 的 `Unpack()`：TBYTE=1 / TWORD=2 / TDWORD=4 /
# TLONG=8（两个 4B：低 + 高）；字符串项是定长 `UnpackString(n)`。
# token 语法沿用 parse_spec：`名字:数字` 整数、`名字:f数字` 定长字符串、
# `名字:数字s` 有符号整数、`名字:?` 变长结构（宽度不可静态定宽）。
_ITEM_ALL_SPEC = (
    'm_Icon:1 m_Shape:2 m_Desc:? m_Sex:1 m_Name:f20 m_Grade:1 '
    'm_Cash:8 m_Saving:4 m_Exp:4 m_NextExp:4 m_NeedExp:4 m_Class:1 '
    'm_Country:1 sOrg:f20 m_EmoteForTitle:1 m_Title:f22 m_HP:2 m_HPMax:2 '
    'm_MP:2 m_MPMax:2 m_FP:2 m_FPMax:2 m_SP:2 m_SPMax:2 '
    'm_Huoli:2 m_Huoli_Max:2 m_Tili:2 m_Tili_Max:2 m_Att_All:2 m_Def_All:2 '
    'm_Spe_All:2 m_Magdef_All:2 m_Str_All:2 m_Mag_All:2 m_Cha_All:2 m_Dex_All:2 '
    'm_Con_All:2 m_Point:2 m_Switch:4 m_GridPoint:1 m_SkillExp:4 m_PFPoint:4 '
    'm_StoryPoint:2 m_Gold:4 m_UI:4 m_DoubleExp:4 m_BuChangExp:4 m_OrgOffer:4 '
    'm_OrgOffer_Max:4 m_OrgCoin:4 m_CookPoint:4 m_FightCash:4 m_ShenWuBi:4 '
    'm_SummonMax:1 m_BagMax:1 m_VIP:1 m_FlyFlag:1 m_Child:4 '
    'm_GradeLimit:1 m_Force:1 m_TitleColor:1 m_RealShape:2 m_XiulianTotalNeed:1 '
    'm_PhotoEffect:4 m_IsGuestLogin:4 m_Birthday:2 m_AutoUpgrade:2 '
    'm_Resist_Xian:1s m_Resist_Gui:1s m_Resist_Yao:1s m_Resist_Zhi:1s '
    'm_Resist_Nian:1s m_At:1 m_Df:1 m_Dp:1'
)
_ITEM_ALL = {i: t for i, t in enumerate(_ITEM_ALL_SPEC.split())}
# 顺序即下标，少一项就会让后面所有属性整体错位 —— 宁可在导入时炸掉，也不要静默错位
assert len(_ITEM_ALL) == 75, 'ITEM_ALL 应为 75 项（0..74），实际 %d' % len(_ITEM_ALL)


def _dyn_prop_change(payload):
    """op17 S2CPropChange 的布局：value 宽度 = ITEM_ALL[type] 的字宽。

    客户端的读法是 `type=UnpackInt(1)` → `prop=ITEM_ALL[type]` → `value=prop.Unpack()`，
    所以宽度是 type 的函数，**不是常量 4B**。内核当前只发 9 个下标（m_HP 16 / m_MP 18 /
    m_FP 20 / m_Gold 43 / m_Exp 8 / m_NextExp 9 / m_At 72 / m_Df 73 / m_Dp 74），
    但客户端能接受任意下标，故按整张表解。

    返回 (字段定义列表 或 None, 说明)；None 表示宽度不能静态确定，调用方应退化成
    「吃掉剩余字节」而不是猜一个宽度。
    """
    if not payload:
        return None, 'payload 为空，type 不可读'
    idx = payload[0]
    tok = _ITEM_ALL.get(idx)
    if tok is None:
        return None, ('type=%d 超出 ITEM_ALL 范围（0..%d），value 宽度未知，按剩余字节显示'
                      % (idx, len(_ITEM_ALL) - 1))
    name = tok.split(':', 1)[0]
    if tok.endswith(':?'):
        return None, ('type=%d → %s 是变长结构（TDESC），宽度不可静态定宽，按剩余字节显示'
                      % (idx, name))
    return parse_spec('type:1 ' + tok), ''


# opcode → 动态布局函数（只对「布局随帧内容变」的编码登记）
def _dyn_map_del(payload):
    """op85 S2CMapDel：长度 8 是「清一块区域」，否则是「删一个对象」。

    客户端 `netmap.S2CMapDel` 第一句就是 `if GetCmdLen() == 8:`，靠**帧体长度**分流，
    两条路径的字段完全不同（8×1B 坐标 vs 4B 对象 id）。
    """
    if len(payload) == 8:
        return parse_spec('sx:1 sy:1 ex:1 ey:1 sx1:1 sy1:1 ex1:1 ey1:1'), '8B 体 = 清区域'
    return parse_spec('id:4'), ''


def _dyn_npc_sign(payload):
    """op101 S2CMapRefreshNpcSign：sub 决定是「图标」还是「需求物品」。"""
    if not payload:
        return None, 'payload 为空，sub 不可读'
    sub = payload[0]
    if sub == 1:
        return parse_spec('sub:1 npcId:4 taskId:4 type:1 color:1'), 'sub=1 图标(type/color)'
    if sub == 2:
        return parse_spec('sub:1 npcId:4 taskId:4 itype:1 shape:4'), 'sub=2 需求物品(itype/shape)'
    return None, 'sub=%d 未登记，按剩余字节显示' % sub


def _dyn_map_effect(payload):
    """op102 S2CMapEffect：mode 决定主体。

    注意 mode 0 / 1-3 / 4 的**总长都是 7B**——静态规格「碰巧吃满」，但字段含义
    完全不同（tid+eid / x+y+eid / tid+hp），必须按 mode 分派才不会把 hp 当 eid 读。
    """
    if not payload:
        return None, 'payload 为空，mode 不可读'
    mode = payload[0]
    table = {
        0: 'mode:1 tid:4 eid:2',
        1: 'mode:1 x:2 y:2 eid:2',
        2: 'mode:1 x:2 y:2 eid:2',
        3: 'mode:1 x:2 y:2 eid:2',
        4: 'mode:1 tid:4 hp:2',
        5: 'mode:1 x:2 y:2 eid:2 times:1 interval:2',
        6: 'mode:1 pid:4 rinc:1 ginc:1 binc:1',
        9: 'mode:1 weather:1',
    }
    spec = table.get(mode)
    if not spec:
        return None, 'mode=%d 未登记，按剩余字节显示' % mode
    return parse_spec(spec), ''


def _dyn_mobile(payload):
    """op58 S2CMobile：sub=1 是手机安全面板回读（2026-10-22）。

    本服的完整布局（`m4_server.build_mobile_info`）：
      sub(1) check(1) receive(1) enable_friends(1) limit(4) degree(4) money(1) balance(2)
    sub≠1 的帧本服不发；长度不对时按剩余字节显示。
    """
    if not payload:
        return None, 'payload 为空，sub 不可读'
    if payload[0] != 1:
        return None, 'sub=%d 未登记（本服只发 sub=1）' % payload[0]
    if len(payload) < 15:
        return None, 'sub=1 需 15B，实际 %dB' % len(payload)
    return parse_spec('sub:1 check:1 receive:1 enable_friends:1 '
                      'limit:T degree:T money:T balance:T'), 'sub=1 手机安全面板回读'


def _dyn_notify_ext(payload):
    """op164 S2CNotifyExt：itype=6 是造型四元组，其余是单个 4B。"""
    if not payload:
        return None, 'payload 为空，itype 不可读'
    if payload[0] == 6:
        return parse_spec('itype:1 p1:4 p2:4 p3:4 p4:4 msg:s'), 'itype=6 造型四元组'
    return parse_spec('itype:1 shape:4 msg:s'), ''


def _dyn_summon_info(payload):
    """op48 S2CSummonInfo：布局随 **帧内容（shape）** 变，不是常量。

    `netpet.S2CSummonInfo` → `pet.UpdateInfo` 先读 pid(4)/name(20)/shape(2)/desc(D)，
    再 `if shape == 65535:` 走删除分支（帧到此为止），否则 `pet.UpdateProp` 继续读
    `BASE_PROP[4:]` 的 70B 属性块。两条路径字段完全不同，静态覆盖表只能覆盖「正常属性面板」，
    故按 shape 分派。
    """
    if len(payload) < 26:
        return None, 'payload 不足 26B，无法读 shape（pid4+name20+shape2）'
    shape = struct.unpack('<H', payload[24:26])[0]
    if shape == 65535:
        return parse_spec('pid:4 name:f20 shape:2 desc:D'), 'shape=65535 删除通知，帧止于 desc'
    return parse_spec('pid:4 name:f20 shape:2 desc:D grade:1 hp:4 mp:4 fp:4 hpmax:4 '
                      'mpmax:4 fpmax:4 damage:4 defense:4 speed:4 magdef:4 str:2 con:2 '
                      'mag:2 cha:2 dex:2 point:2 exp:4 expnext:4 expneed:4 life:4 molingguo:1'), ''


# op65 S2CSkillLevel 的 sktype 分支附加字段（以客户端 nethero.S2CSkillLevel 逐行为准）
_SKILL_LEVEL_EXTRA = {
    1: 'sklimit:1 truelv:1 point:4 point_max:4',
    2: 'bid:2 point:2 cash:4 iHighLv:1',     # 本服 build_skill_level_magic 走这条
    3: 'bid:2',
    5: 'color:1 candel:1 title:s',
    6: 'magicid:2',
}


def _dyn_summon_skill(payload):
    """op49 S2CSummonSkill：技能 id 列表长度随 n 变（客户端 netpet.S2CSummonSkill）。

    布局：`pid(4) n(1) x(1) + skill_id(2) × n`；客户端 `for i in xrange(n)` 逐个读，
    并用 `hfskill = n - x` 区分「护符技能」（idx >= hfskill 的那几个）。
    n 随帧内容变（本服发 PET_SKILLS 共 6 条），静态规格只能把整个列表塞进 `T` 剩余块——
    本处理器按 payload 实解，展开成 `sk0..sk(n-1)` 具名字段。
    """
    if len(payload) < 6:
        return None, 'payload 不足 6B，无法读 n/x（pid4+n1+x1）'
    n, x = payload[4], payload[5]
    ids = ' '.join('sk%d:2' % i for i in range(n))
    spec = 'pid:4 n:1 hf_from:1' + ((' ' + ids) if ids else '')
    hf = max(0, n - x)                    # 客户端 hfskill = n - x（护符技能数）
    return parse_spec(spec), '技能 %d 个（护符 %d 个 = n-x）' % (n, hf)


def _dyn_skill_level(payload):
    """op65 S2CSkillLevel：布局随 sktype 变（客户端 nethero.S2CSkillLevel 八分支）。

    公共前缀：`sktype(1)`；`sktype != 10` 时读 `skid(2) sklevel(1)`，否则读
    `exp(2) expnext(2)` 即止（心法经验专用，不读 skid/sklevel）。
    分支附加字段（见 _SKILL_LEVEL_EXTRA）：
      1=技能修炼(sklimit/truelv/point/point_max)  2=学招式(bid/point/cash/iHighLv)
      3=技能格(bid)  5=称号(color/candel/title 变长)  6=快捷键(magicid)
      7=装备进战斗菜单、9=选中、其它=无附加字段（帧止于 sklevel）
    ★ sktype=6 且 sklevel != 1 时客户端提前 return 不读 magicid——本服不发 6，
      规格按读完处理，真遇到时 note 会说明。
    """
    if not payload:
        return None, 'payload 为空，sktype 不可读'
    sktype = payload[0]
    if sktype == 10:
        return parse_spec('sktype:1 exp:2 expnext:2'), 'sktype=10 心法经验分支（不读 skid/sklevel）'
    extra = _SKILL_LEVEL_EXTRA.get(sktype, '')
    spec = 'sktype:1 skid:2 sklevel:1' + ((' ' + extra) if extra else '')
    note = '' if extra else 'sktype=%d 无附加字段（帧止于 sklevel）' % sktype
    return parse_spec(spec), note


# op96 S2CMapSceneM 的两种特殊场景标记（客户端 netmap.S2CMapSceneM 首字段分派）
_SCENE_SPECIAL = 65535     # 特殊场景（战斗图等）：mark + 真实 scene + 6 个场景参数
_SCENE_MAZE = 65534        # 迷宫场景：mark + 真实 scene + 房间/道路参数 + 不可通行边表
_SCENE_TAIL = 'mapid:2 minimap:2 x:2 y:2 name:s'   # 三种布局的公共尾


def _dyn_map_scene_m(payload):
    """op96 S2CMapSceneM：scene 首字段分派三种布局（客户端 netmap.S2CMapSceneM）。

    · 普通场景：`scene(2) mapid(2) minimap(2) x(2) y(2) name(变长)`
      （= 静态覆盖表规格；本服 build_scene_m 只发这种，SCENE_ID=1）
    · scene==65535 特殊场景：`mark(2) scene(2) arg0(4) arg1(2) arg2(2) arg3(2) arg4(1) arg5(1)`
      + 公共尾（mark 恒 65535，其后 scene 才是真实场景号）
    · scene==65534 迷宫场景：`mark(2) scene(2) rows(1) cols(1) roomw(1) roomh(1) roadw(1)
      roadh(1) mazetype(1) n(2)` + **n 组不可通行边 [a(1) b(1)]** + 公共尾；
      客户端 `for i in xrange(n): a=…; if a<=0: break; b=…`——边表组数随内容变，
      本处理器按 payload 实解（复刻提前结束语义）后展开成显式字段。
    ★ 特殊/迷宫布局只可能出现在**官方服抓包**里（本服不发）；配它是为了让调试台
      解码官方流量时不错位，同时把线序对账的「需动态未配」清零。
    """
    if len(payload) < 2:
        return None, 'payload 不足 2B，无法读 scene'
    mark = struct.unpack('<H', payload[:2])[0]
    if mark == _SCENE_SPECIAL:
        return (parse_spec('mark:2 scene:2 arg0:4 arg1:2 arg2:2 arg3:2 arg4:1 arg5:1 '
                           + _SCENE_TAIL),
                'scene=65535 特殊场景（mark + 真实 scene + 6 个场景参数）')
    if mark == _SCENE_MAZE:
        if len(payload) < 13:
            return None, 'payload 不足 13B，无法读迷宫参数（mark2+scene2+7×1B+n2）'
        n = struct.unpack('<H', payload[11:13])[0]
        # 复刻客户端边表解析：读 a，a<=0 即止——**该 a 字节已被消费**（只是不读 b），
        # 规格里必须留一个 a_end 字段，否则公共尾整体错位（2026-10-19 实测踩过）。
        pos, k, term = 13, 0, False
        for _i in range(n):
            if pos >= len(payload):
                break
            a = payload[pos]
            pos += 1
            if a <= 0:
                term = True
                break
            if pos >= len(payload):
                break
            pos += 1                      # b
            k += 1
        edges = ' '.join('ea%d:1 eb%d:1' % (i, i) for i in range(k))
        if term:                          # 提前结束的终止字节（a<=0，已被客户端消费）
            edges = (edges + ' a_end:1') if edges else 'a_end:1'
        spec = ('mark:2 scene:2 rows:1 cols:1 roomw:1 roomh:1 roadw:1 roadh:1 '
                'mazetype:1 n:2' + ((' ' + edges) if edges else '') + ' ' + _SCENE_TAIL)
        note = 'scene=65534 迷宫场景（%d 组不可通行边%s）' % (k, '，a<=0 提前结束' if term else '')
        return parse_spec(spec), note
    return parse_spec('scene:2 ' + _SCENE_TAIL), ''


_OV_DYN = {17: _dyn_prop_change, 48: _dyn_summon_info, 49: _dyn_summon_skill,
           58: _dyn_mobile,
           65: _dyn_skill_level, 85: _dyn_map_del, 96: _dyn_map_scene_m,
           101: _dyn_npc_sign, 102: _dyn_map_effect, 164: _dyn_notify_ext}


# ══════════════════════════════════════════════════════════ 协议字典 ══
def _iter_funcs(doc):
    """遍历 protocol_functions.json 的全部函数条目（附所属模块名）。"""
    for _fname, body in (doc.get('modules') or {}).items():
        mod = body.get('module') or ''
        for f in body.get('functions') or []:
            f = dict(f)
            f['_module'] = mod
            yield f


def _parse_s2c_routes(net_py_text):
    """从客户端 net.py 抽取 S2C 派发表：opcode → (模块, 处理函数)。

    `NET_FUNCTION_NAME` 是客户端 `CNet.__init__` 填 `m_NetFunction` 的唯一来源，
    所以它就是 S2C opcode 的权威解释。
    """
    a = net_py_text.find('NET_FUNCTION_NAME')
    if a < 0:
        return {}
    b = net_py_text.find('SPECIAL_SERVER_CMD')
    block = net_py_text[a:b if b > a else len(net_py_text)]
    routes = {}
    for op, mod, fn in re.findall(
            r'(\d+)\s*:\s*\(\s*[\'"]([\w\.]+)[\'"]\s*,\s*[\'"]([\w]+)[\'"]', block):
        routes[int(op)] = (mod, fn)
    return routes


def _parse_c2s_consts(net_py_text):
    """从客户端 net.py 抽取上行 opcode 常量：opcode → 常量名。

    上行有**两族前缀**，都要认：
      `C2S_*`   客户端 → 服务端
      `C2GS_*`  客户端 → GameServer（同样是客户端发出去的）
    `GS2C_*` 是反方向，不算上行（但它可能与上行共用 opcode，如 174 / 181）。

    ★ 这份常量表才是「客户端真的会发哪些编码」的权威口径。字典与全表清单都可能
      与它不一致——实测 `protocol_c2s_full.txt` 的 120 条把 `C2GS_*` 那批整个漏掉了，
      而**分母少了会直接让「待实现清单」漏项，使用者会漏做工作**。所以分母取这里，
      字典降级为「字段定义来源」，三方差异另作对账显式暴露。
    """
    out = {}
    for m in re.finditer(r'^\s*((?:C2S|C2GS)_\w+)\s*=\s*(\d+)\s*$', net_py_text, re.M):
        out.setdefault(int(m.group(2)), m.group(1))
    return out


class Protocol:
    """协议字典：opcode ↔ 名称 / 模块 / 字段布局（覆盖表优先，字典兜底）。

    加载失败**不抛异常**：退化成空字典，抓包照常工作（只剩 hex 视图）。
    调试工具自己先崩掉是最没用的失败方式。
    """

    def __init__(self, dict_path=DICT_PATH, net_py_path=NET_PY_PATH):
        self.ok = False
        self.error = ''
        self.c2s_variants = {}     # opcode -> [函数定义, ...]（同 opcode 多种调用形态）
        self.c2s_upspace = {}      # opcode -> 常量名（客户端上行全集，覆盖率分母）
        self.s2c_routes = {}       # opcode -> (module, handler)
        self.s2c = {}              # (module, handler) -> 函数定义
        try:
            self._load(dict_path, net_py_path)
            self.ok = True
        except (OSError, ValueError, KeyError) as e:      # noqa: BLE001
            self.error = '%s：%r' % (os.path.basename(dict_path), e)

    def _load(self, dict_path, net_py_path):
        with io.open(dict_path, encoding='utf-8') as f:
            doc = json.load(f)
        for fn in _iter_funcs(doc):
            if fn.get('dir') == 'C2S' and fn.get('opcode') is not None:
                self.c2s_variants.setdefault(int(fn['opcode']), []).append(fn)
            elif fn.get('fields') is not None:
                self.s2c[(fn.get('_module') or '', fn.get('name'))] = fn
        with io.open(net_py_path, encoding='utf-8') as f:
            net_text = f.read()
        self.s2c_routes = _parse_s2c_routes(net_text)
        self.c2s_upspace = _parse_c2s_consts(net_text)

    # ── 查询 ──
    def describe(self, direction, op, payload=None):
        """返回 {name, module, layouts, src, known, variants}。

        layouts 是**候选布局列表**，解码器会挑最贴合 payload 的那一种；
        src ∈ {'overlay', 'dict', 'none'} 说明布局来自哪一层。

        `payload` 可选：只有「布局由帧内容决定」的编码需要它（如 op17 的 value
        宽度随 type 变）。不传就退回静态规格——那只是占位，别拿它判对错。
        """
        ov = OVERLAY.get(direction, {}).get(op)
        if ov is not None:
            info = {'name': OVERLAY_NAME[direction][op], 'module': '',
                    'layouts': [ov], 'src': 'overlay', 'known': True,
                    'variants': 1,
                    'hint': _OV_NOTE.get(op, '') if direction == 'S2C' else ''}
            dyn = _OV_DYN.get(op) if direction == 'S2C' else None
            if dyn is not None and payload is not None:
                fields, dyn_hint = dyn(payload)
                # 宽度不确定时退化成「吃掉剩余字节」：既不越界，也不假装知道宽度
                info['layouts'] = [fields or parse_spec('type:1 value:T')]
                if dyn_hint:
                    info['hint'] = ((info['hint'] + '；') if info['hint'] else '') + dyn_hint
            return info
        if direction == 'C2S':
            vs = self.c2s_variants.get(op) or []
            if not vs:
                # 字典没字段定义 ≠ 客户端不会发。分开报，别混成一句「字典无此编码」：
                # op8(C2S_PING) 就是客户端确实会发、字典却没条目的例子。
                const = self.c2s_upspace.get(op)
                if const:
                    return {'name': const, 'module': '', 'layouts': [],
                            'src': 'client', 'known': True, 'variants': 0,
                            'hint': '客户端会发，但字典缺字段定义 → 只能看 hex'}
                return {'name': '', 'module': '', 'layouts': [], 'src': 'none',
                        'known': False, 'variants': 0, 'hint': ''}
            layouts = [v.get('fields') or [] for v in vs]
            # 字段多的排前面：信息量更大，试配时也更可能吃满 payload
            layouts.sort(key=len, reverse=True)
            return {'name': vs[0].get('opcode_const') or vs[0].get('name') or '',
                    'module': vs[0].get('_module') or '', 'layouts': layouts,
                    'src': 'dict', 'known': True, 'variants': len(vs), 'hint': ''}
        route = self.s2c_routes.get(op)
        fn = self.s2c.get(route) if route else None
        if not route:
            return {'name': '', 'module': '', 'layouts': [], 'src': 'none',
                    'known': False, 'variants': 0, 'hint': ''}
        layouts = [fn.get('fields') or []] if fn else []
        hint = ''
        if fn and fn.get('args') == 'netobj':
            hint = ('字典此条是「分发器展开」形态（args=netobj），字段数不代表线上布局；'
                    '若下方 tail 非空即属正常')
        return {'name': (fn or {}).get('name') or route[1], 'module': route[0],
                'layouts': layouts, 'src': 'dict', 'known': True, 'variants': 1,
                'hint': hint}

    def table(self, direction, overlay_only=False):
        """某方向的完整编码表（覆盖率页用），opcode 升序。

        C2S 的分母取**客户端上行常量全集**（net.py 的 `C2S_*` + `C2GS_*`），字典只用来
        补字段定义。反过来以字典当分母会漏项——实测字典 131 条、报告手写 137 条，
        而客户端实际能发 136 条左右，谁都不等于谁；只有客户端源码是权威。

        `src` 说明该行两个来源的命中情况：
          client+dict  两边都有（正常）
          client       客户端会发，但字典缺字段定义 → 出现时只能看 hex，需补字典
          dict         字典有、客户端却找不到常量 → 多为幻影/已废弃，须人工确认

        注意「字典缺字段定义」与「服务端未实现」是**两个独立维度**：前者是逆向覆盖的
        缺口，后者由内核日志旁听（`（未识别 op=N）`）给出运行时证据。op 8（C2S_PING）
        就是服务端早已实现、字典里却查不到的例子，不该混成一句「未实现」。
        """
        out = []
        if direction == 'C2S':
            for op in sorted(set(self.c2s_upspace) | set(self.c2s_variants)):
                vs = self.c2s_variants.get(op) or []
                const = self.c2s_upspace.get(op) or ''
                if const and vs:
                    src = 'client+dict'
                elif const:
                    src = 'client'
                else:
                    src = 'dict'
                out.append({'op': op,
                            'name': const or (vs[0].get('opcode_const')
                                              or vs[0].get('name') or ''),
                            'module': (vs[0].get('_module') or '') if vs else '',
                            'fields': (max(len(v.get('fields') or []) for v in vs)
                                       if vs else 0),
                            'variants': len(vs), 'src': src})
        else:
            for op in sorted(set(self.s2c_routes) | set(OVERLAY.get('S2C', {}))):
                if op in OVERLAY.get('S2C', {}):
                    out.append({'op': op, 'name': OVERLAY_NAME['S2C'][op], 'module': '',
                                'fields': len(OVERLAY['S2C'][op]), 'variants': 1,
                                'src': 'overlay'})
                else:
                    mod, handler = self.s2c_routes[op]
                    fn = self.s2c.get((mod, handler))
                    out.append({'op': op, 'name': handler, 'module': mod,
                                'fields': len((fn or {}).get('fields') or []),
                                'variants': 1, 'src': 'dict'})
        if overlay_only:
            out = [r for r in out if r['src'] == 'overlay']
        return out


# ══════════════════════════════════════════════════════════ 帧分界 ══
_MALFORMED = object()          # 帧头非法（长度为 0），需跳 1 字节重新同步


def _parse_one(direction, buf, cur, n):
    """尝试解析一帧。字节不足返回 None（**一个字节都不消费**），帧头非法返回 _MALFORMED。

    关键约束：只有完整帧才允许推进游标，否则尾部半帧会在下次 feed 时丢头。
    """
    if direction == 'C2S':                   # [op][len][body]
        if cur + 2 > n:
            return None
        op = buf[cur]
        ln = buf[cur + 1]
        head = cur + 2
    else:                                    # [len][op][data]
        if cur + 1 > n:
            return None
        ln = buf[cur]
        head = cur + 1
    if ln == 0xFF:                           # 扩展长度：0xFF + u16
        if head + 2 > n:
            return None
        ln = struct.unpack('<H', bytes(buf[head:head + 2]))[0]
        head += 2
    # S2C 的 ln 含 opcode，C2S 的 ln 不含 —— 两种帧格式唯一的语义差异。
    # 因此 ln==0 只管 S2C 非法；C2S 的 ln==0 是合法的空体帧（PING / 防御 / 逃跑…）。
    if direction != 'C2S' and ln == 0:
        return _MALFORMED
    if head + ln > n:                        # body 未到齐
        return None
    if direction == 'C2S':
        return op, bytes(buf[head:head + ln]), head + ln
    # ★ S2C 加密通道下帧体可能是 LZSS 压缩流（make_s2c 对 opcode+data 整体压缩，
    #   长度前缀=压缩后长度）：解析前先做往返校验解压；判别不了就原样上交，
    #   「偏差不隐藏」原则不变。游标推进仍按原始帧长（分帧与压缩层无关）。
    payload = _maybe_lzss(bytes(buf[head:head + ln]))
    return payload[0], payload[1:], head + ln


def split_frames(direction, buf):
    """把字节流切成完整帧。

    返回 ([(原始字节, opcode, payload), ...], 剩余半帧)。
    **全项目唯一的分帧实现**：抓包、丢弃、注入都走这里，避免多套实现互相漂移。

    帧头出现长度为 0 这种非法值时跳 1 字节重新同步（真实流不该出现；一旦出现，
    后续帧会以「未知 opcode」的形式暴露给使用者，这正是排查失步所需要的）。
    """
    out, cur, n = [], 0, len(buf)
    while cur < n:
        got = _parse_one(direction, buf, cur, n)
        if got is _MALFORMED:
            cur += 1
            continue
        if got is None:
            break
        op, body, nxt = got
        out.append((bytes(buf[cur:nxt]), op, body))
        cur = nxt
    return out, bytes(buf[cur:])


class Reassembler:
    """流式分帧器：攒够一帧就吐出来（split_frames 的有状态外壳）。

    还带一层**失步自愈**：正常流里积压超过 MAX_HOLD 却一帧都切不出来，说明已经
    错位（例如某段非帧字节混进了流）。这时逐字节前移重新对齐，而不是把缓冲
    无限涨下去、把之后所有帧都吞掉。跳过的字节数会上报，界面里看得见。
    """

    MAX_HOLD = 4096
    MAX_SKIP = 1024

    def __init__(self, direction):
        self.dir = direction
        self.buf = b''

    def feed(self, chunk):
        """返回 ([(opcode, payload), ...], 本次跳过的失步字节数)。"""
        self.buf += chunk
        parts, rest = split_frames(self.dir, self.buf)
        if parts:
            self.buf = rest
            return [(op, body) for _raw, op, body in parts], 0
        if len(self.buf) <= self.MAX_HOLD:
            self.buf = rest
            return [], 0
        for skip in range(1, min(len(self.buf), self.MAX_SKIP)):
            parts, rest = split_frames(self.dir, self.buf[skip:])
            if parts:
                self.buf = rest
                return [(op, body) for _raw, op, body in parts], skip
        dropped = len(self.buf)
        self.buf = b''
        return [], dropped


# ══════════════════════════════════════════════════════════ 字段解码 ══
# 客户端 `prop.UnpackDesc` 的标签 → 后续字节数（1B 标签 + N 字节数据）。
# 逐条对齐 src/prop.py:43-108：t=5 走 `xrange(t/3*3)` → 3B；t=6 → 6B；
# 未列出的标签（如 11）客户端不读任何字节，这里也按 0 处理。
_DESC_TAG_LEN = {0: 3, 1: 2, 2: 3, 3: 1, 4: 2, 5: 3, 6: 6, 7: 1, 8: 1, 9: 1,
                 10: 2, 11: 0, 12: 1, 13: 2, 14: 2, 15: 2, 16: 2, 17: 2,
                 18: 2, 19: 2}


def _num(size, default=0):
    s = str(size).strip()
    return int(s) if s.isdigit() else default


def decode_fields(fields, payload):
    """按一种字段布局解析 payload。

    返回 (rows, tail, note, consumed)：
      rows     每项 {i, name, kind, size, value, disp, hex}
      tail     定义之外的剩余字节（非空 = 布局与实际不符，是协议还原的线索）
      note     越界 / 类型不支持 / 截断等说明
      consumed 实际吃掉的字节数（供多布局试配打分）
    """
    rows, cur, note = [], 0, ''
    data = bytes(payload)
    total = len(data)
    seen = {}                              # 已解出的整数字段：名字 → 值（供 P 取宽度）
    for i, f in enumerate(fields or []):
        kind = str(f.get('kind') or '?')
        size = f.get('size')
        signed = bool(f.get('signed'))
        bf_extra = []                          # 位域容器拆出来的子行
        row = {'i': i, 'name': f.get('value') or '', 'kind': kind,
               'size': str(size), 'signed': signed}
        if kind in ('I', 'R'):
            n = _num(size)
            if n <= 0:
                note = '字段 %d 为变长数值，静态不可解' % i
                rows.append(row)
                continue
            if cur + n > total:
                # ★ 必须同时写 note：否则「被截断的布局」会以 consumed==total 且无异常
                #   的身份在试配里胜出，把真正的精确布局挤掉（这里踩过）。
                note = '字段 %d 越界（需 %dB 剩 %dB）' % (i, n, total - cur)
                row.update({'value': None, 'disp': '← 越界（需 %dB 剩 %dB）'
                            % (n, total - cur)})
                rows.append(row)
                cur = total
                break
            raw = data[cur:cur + n]
            row['hex'] = ' '.join('%02X' % b for b in raw[:32])
            if kind == 'R':                            # 定长原始块：不假装它是数
                row['value'] = raw.hex()
                row['disp'] = '%dB 原始块 %s…' % (n, raw[:8].hex(' '))
            else:
                v = int.from_bytes(raw, 'little', signed=signed)
                row['value'] = v
                row['disp'] = str(v)
                if not signed and n == 1 and v > 127:
                    row['disp'] = '%d (s8 %d)' % (v, v - 256)
                elif not signed and n == 2 and v > 32767:
                    row['disp'] = '%d (s16 %d)' % (v, v - 65536)
            cur += n
        elif kind == 'S':
            n = _num(size)
            if n > 0:                                  # 定长字符串（名字/密码…）
                if cur + n > total:
                    note = '字段 %d 字符串越界（需 %dB 剩 %dB）' % (i, n, total - cur)
                    rows.append(row)
                    cur = total
                    break
                raw = data[cur:cur + n]
                cur += n
            else:                                      # 变长 C 字符串（GBK + \0）
                end = data.find(b'\x00', cur)
                if end < 0:
                    note = '变长字符串缺 \\0 结尾（可能被截断）'
                    raw, end = data[cur:], total
                else:
                    raw = data[cur:end]
                    end += 1
                cur = end
            row['hex'] = ' '.join('%02X' % b for b in raw[:32])
            row['value'] = raw.decode('gbk', 'replace')
            row['disp'] = repr(row['value'])
        elif kind == 'V':                              # 1B 长度前缀的变长字符串
            if cur >= total:
                note = '字段 %d 长度前缀越界' % i
                rows.append(row)
                break
            n = data[cur]
            cur += 1
            if cur + n > total:
                note = '字段 %d 变长字符串越界（前缀 %dB 剩 %dB）' % (i, n, total - cur)
                rows.append(row)
                cur = total
                break
            raw = data[cur:cur + n]
            cur += n
            row['hex'] = ' '.join('%02X' % b for b in raw[:32])
            row['value'] = raw.decode('gbk', 'replace')
            row['disp'] = repr(row['value'])
        elif kind == 'D':                              # 外观描述：标签联合
            start = cur
            if cur >= total:
                note = '字段 %d desc 越界（缺条目数）' % i
                rows.append(row)
                break
            n = data[cur]
            cur += 1
            items = []
            ok = True
            for _k in range(n):
                if cur >= total:
                    note = '字段 %d desc 第 %d 条越界（缺标签）' % (i, _k)
                    ok = False
                    break
                tag = data[cur]
                cur += 1
                ln = _DESC_TAG_LEN.get(tag, 0)
                if cur + ln > total:
                    note = '字段 %d desc 标签 %d 越界（需 %dB 剩 %dB）' % (i, tag, ln, total - cur)
                    ok = False
                    cur = total
                    break
                items.append('%d=%s' % (tag, data[cur:cur + ln].hex(' ')) if ln else '%d' % tag)
                cur += ln
            row['hex'] = ' '.join('%02X' % b for b in data[start:cur][:32])
            row['value'] = None
            row['disp'] = ('desc[%d]{%s}' % (n, ' '.join(items))) if n else 'desc[0]'
            if not ok:
                rows.append(row)
                break
        elif kind == 'P':                              # 宽度由前面某字段给的定长串
            ref = str(size)
            n = seen.get(ref)
            if not isinstance(n, int):
                note = '字段 %d 依赖 %r 的长度，但该字段未解出' % (i, ref)
                rows.append(row)
                break
            if n <= 0:
                row['hex'] = ''
                row['value'] = ''
                row['disp'] = "''"
            elif cur + n > total:
                note = '字段 %d 字符串越界（需 %dB 剩 %dB）' % (i, n, total - cur)
                rows.append(row)
                cur = total
                break
            else:
                raw = data[cur:cur + n]
                cur += n
                row['hex'] = ' '.join('%02X' % b for b in raw[:32])
                row['value'] = raw.decode('gbk', 'replace')
                row['disp'] = repr(row['value'])
        elif kind == 'B':                              # 位域容器：1B 拆多个位
            if cur >= total:
                note = '字段 %d 位域越界' % i
                rows.append(row)
                break
            raw = data[cur]
            cur += 1
            row['hex'] = '%02X' % raw
            row['value'] = raw
            parts = []
            for (bname, pos) in (f.get('bits') or []):
                val = (raw >> pos) & 1
                parts.append('%s=%d' % (bname, val))
                bf_extra.append({'i': i, 'name': bname, 'kind': 'B', 'size': '1',
                                 'value': val, 'disp': 'bit%d=%d' % (pos, val)})
            row['disp'] = '0x%02X（%s）' % (raw, ' '.join(parts))
        elif kind == 'T':                              # 吃掉剩余全部字节
            raw = data[cur:]
            cur = total
            row['hex'] = ' '.join('%02X' % b for b in raw[:32])
            row['value'] = '%dB' % len(raw)
            row['disp'] = '%s…' % raw[:16].hex(' ') if raw else '（空）'
        elif kind == 'L':                              # ★ 循环构造（2026-10-20）
            # control 是字符串：
            #   'sentinel:shape' → 循环直到 sub_fields 里 name==control 的字段值 == 0
            #   'count:n'        → 循环 n 次（n 来自前面已解出的计数字段）
            ctrl = str(f.get('control') or '')
            sub = f.get('sub_fields') or []
            if not sub:
                note = '字段 %d 循环无子字段定义' % i
                rows.append(row)
                break
            entries = []
            loop_idx = 0
            max_loops = 256     # 防止畸形帧无限循环
            sentinel_name = None
            count_val = None
            if ctrl.startswith('sentinel:'):
                sentinel_name = ctrl[len('sentinel:'):].strip()
            elif ctrl.startswith('count:'):
                count_ref = ctrl[len('count:'):].strip()
                count_val = seen.get(count_ref)
                if not isinstance(count_val, int) or count_val < 0:
                    note = '字段 %d 循环计数 %r 未解出或非正整数' % (i, count_ref)
                    rows.append(row)
                    break
            while cur < total and loop_idx < max_loops:
                if count_val is not None and loop_idx >= count_val:
                    break
                # ★ 哨兵检测在解完整循环体**之前**做（2026-10-20）：
                #   哨兵行通常只有「哨兵字段 = 0」若干字节，不是完整循环体。
                #   如果先解完整体，剩余字节不够 → 报「未吃满」就错了。
                #   这里 peek 哨兵字段：在 sub_fields 里找 name == sentinel_name，
                #   只解它的宽度，值 == 0 就结束循环。
                if sentinel_name:
                    _sf = next((f for f in sub if f.get('value') == sentinel_name), None)
                    if _sf:
                        _sz = int(_sf.get('size') or '1')
                        if cur + _sz <= total:
                            _sv = int.from_bytes(data[cur:cur + _sz], 'little')
                            if _sv == 0:
                                cur += _sz       # 吃掉哨兵字段
                                break           # 哨兵：结束循环
                # 解完整循环体
                sub_rows, sub_tail, sub_note, sub_consumed = \
                    decode_fields(sub, data[cur:])
                if sub_note:
                    note = '字段 %d 循环第 %d 条：%s' % (i, loop_idx, sub_note)
                    rows.append(row)
                    cur = total
                    break
                if sub_tail:
                    # ★ 有 tail 是正常的：剩余字节可能就是哨兵或下一条数据。
                    #   哨兵检测会在下一次 while 迭代处理。不报错、不 break。
                    pass
                entries.append({r.get('name'): r.get('value')
                               for r in sub_rows if r.get('name')})
                cur += sub_consumed
                loop_idx += 1
            row['hex'] = ''
            row['value'] = entries
            # ★ disp 用「key=value」紧凑写法（2026-10-20）：
            #   原先直接 str(dict)，单引号+花括号在调试台里像原始数据、不像字段视图。
            #   现在每条渲染成 `{style=0 shape=4005 name=梦想企鹅}`，多条目用 ` | ` 分隔。
            def _fmt_entry(e):
                return '{%s}' % ' '.join(
                    '%s=%s' % (k, '' if v is None else v)
                    for k, v in e.items())
            _shown = ' | '.join(_fmt_entry(e) for e in entries[:3])
            row['disp'] = '[%d] %s%s' % (
                len(entries), _shown,
                ' …' if len(entries) > 3 else '')
            # ★ 顺序：先让通用 rows.append(row) 追加汇总行，再补逐条展开行。
            #   旧写法把展开行 append 在这里，导致 'entries[0]' 排在 'entries' 之前，
            #   界面上看着像「先有明细后有汇总」，读起来别扭。
            #   这里改成：把展开行暂存到 row['_expand']，由外层统一在 row 之后追加。
            row['_expand'] = [
                {'name': '%s[%d]' % (row['name'], e_idx),
                 'kind': 'L', 'size': '0', 'value': e,
                 'hex': '', 'disp': _fmt_entry(e)}
                for e_idx, e in enumerate(entries)]
        elif kind == 'Bit':                            # size 形如 "5@2" = 第 5 位在 2B 里
            m = re.match(r'(\d+)@(\d+)', str(size))
            idx, n = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
            if cur + n > total:
                note = '字段 %d 位域越界' % i
                rows.append(row)
                cur = total
                break
            raw = data[cur:cur + n]
            row['hex'] = ' '.join('%02X' % b for b in raw)
            v = int.from_bytes(raw, 'little')
            row['value'] = (v >> idx) & 1
            row['disp'] = 'bit%d=%d（整值 %d）' % (idx, row['value'], v)
            cur += n
        else:                                          # 未知 kind：不猜，原地停
            note = '字段 %d 类型 %r 未支持' % (i, kind)
            rows.append(row)
            break
        if isinstance(row.get('value'), int):
            seen[row.get('name') or ('#%d' % i)] = row['value']
        rows.append(row)
        rows.extend(bf_extra)
        # ★ 循环展开行：在汇总行之后追加，保证「先汇总、后明细」的阅读顺序
        if row.get('_expand'):
            rows.extend(row.pop('_expand'))
    return rows, data[cur:], note, cur


def decode_best(layouts, payload):
    """在多种候选布局里挑最贴合 payload 的一种，返回解码结果。

    打分：**恰好吃满且无异常** > 吃得越多越好；越界（负分）永不入选。
    没有任何布局可用时退化为「全字节 tail」，不编造字段。

    ★ 2026-10-19 修：`fit` 原先只看 `consumed == total`，而解码器在越界时会把
    `cur` 强推到 `total`（为了如实展示已读部分），于是**越界帧的 consumed 恰好等于
    total、tail 为空、fit=True**——「吃到一半撞墙」被报成「完美吃满」，只剩 note 是证据，
    而 `fit` 字段才是调用方（调试台标色、巡检判定）真正看的东西。
    字节翻转 fuzz 实测过：940 次单字节变异里有 22 次落到这个自相矛盾状态。
    现在 `fit` 额外要求「无 note」——**报了异常就不算吃满**。
    """
    total = len(payload)
    if not layouts:
        # 没有候选布局 ≠ 布局不符：前者是「字典缺字段定义」，后者是「解得对不上」。
        # fit 用 None 表示「不适用」——界面判的是 `fit === false`，null 不会命中，
        # 于是这类帧不会被打上黄色的「布局不符」，那原本是误伤。
        return {'rows': [], 'tail': bytes(payload),
                'note': '无候选布局（字典缺字段定义，只能看 hex）',
                'tail_len': total, 'layout': -1, 'n_layouts': 0, 'fit': None}
    best = None
    for i, fields in enumerate(layouts):
        rows, tail, note, consumed = decode_fields(fields, payload)
        if consumed > total:
            score = -1
        elif consumed == total and not note:
            score = 10000 + len(rows)        # 恰好吃满 + 干净：最高优先级
        elif consumed == total:
            score = 5000 + len(rows)
        else:
            score = consumed
        if best is None or score > best[0]:
            best = (score, i, rows, tail, note, consumed)
    score, idx, rows, tail, note, consumed = best
    if score < 0:
        return {'rows': [], 'tail': bytes(payload), 'note': '字典布局与 payload 不兼容',
                'tail_len': total, 'layout': -1, 'n_layouts': len(layouts), 'fit': False}
    # ★ 越界/截断时 consumed 会被强推到 total，单看它会把「撞墙」误判为「吃满」。
    #   note 非空 ⇒ 解析过程中出过异常 ⇒ 不算 fit（tail 仍按实际剩余字节如实给出）。
    fit = (consumed == total) and not note and not tail
    if len(layouts) > 1:
        note = (('%s；' % note) if note else '') \
               + '多形态择一：#%d/%d（吃满=%s）' % (idx, len(layouts), '是' if fit else '否')
    elif not fit and not note:
        note = '布局未吃满：定义 %d 项吃掉 %dB，剩余 %dB' % (len(rows), consumed, total - consumed)
    return {'rows': rows, 'tail': tail, 'note': note, 'tail_len': len(tail),
            'layout': idx, 'n_layouts': len(layouts), 'fit': fit}


# ══════════════════════════════════════════════════════════ 帧构造 ══
def make_s2c(opcode, data=b''):
    """S2C 帧：[1B N][opcode][data]，N 含 opcode（与 m4_server.make_s2c 逐字节一致）。"""
    payload = bytes([opcode]) + bytes(data)
    if len(payload) >= 0xFF:
        return b'\xFF' + struct.pack('<H', len(payload)) + payload
    return bytes([len(payload)]) + payload


def make_c2s(opcode, data=b''):
    """C2S 帧：[opcode][1B N][body]，N 不含 opcode。"""
    body = bytes(data)
    if len(body) >= 0xFF:
        return bytes([opcode]) + b'\xFF' + struct.pack('<H', len(body)) + body
    return bytes([opcode, len(body)]) + body


# ══════════════════════════════════════════════════════════ 连接包装 ══
class TappedConn:
    """鸭子类型的 conn 包装：转发全部字节，同时喂给 FrameTap。

    内核只用到 `sendall / recv / settimeout / close`；其余属性经 __getattr__ 透传，
    所以 `_gm_send`、`recv_exact` 等既有调用**一行都不用改**。

    丢弃语义（`FrameTap.drop_next`）按帧生效：
      · S2C 丢弃 → 客户端收不到这一帧（测客户端缺帧容错）
      · C2S 丢弃 → 内核收不到这一帧；若整块都被吞就再读一次，
        而不是返回 b''——那会被 `recv_exact` 误判成连接断开。
    """

    def __init__(self, conn, tap):
        self._conn = conn
        self._tap = tap
        self._hold = {'C2S': b'', 'S2C': b''}    # 半帧暂存（丢弃时用）
        self._in_buf = b''                        # 已读进来、还没交给内核的字节

    # ── 内核用到的四件套 ──
    def sendall(self, data):
        data = self._filter_drop('S2C', data)
        if not data:
            return None
        self._tap.feed('S2C', data)
        return self._conn.sendall(data)

    def recv(self, n):
        """带注入端口的缓冲接收器。

        对调用方保持「要么给满 n 字节、要么在超时后抛 socket.timeout、要么 EOF
        返回 b''」的语义（内核唯一的消费者 `recv_exact` 正是这么用的），内部则
        用 0.1s 分片读底层：

        ★ 为什么必须分片：裸 `socket.recv` 一旦阻塞就无法从外部唤醒，此时注入的
          伪造帧只能等客户端下次自发说话才生效——调试时表现为「点了注入，内核没反应」。
          分片让每一片之间都回到这里检查注入队列。分片超时**不会**外泄给调用方
          （否则 `recv_exact` 正在攒的半帧会被丢掉，直接串帧），只在本层内部重试，
          直到调用方自己的超时到点。
        """
        if n <= 0:
            return b''
        try:
            t = self._conn.gettimeout()
        except (AttributeError, OSError):
            t = None
        deadline = None if t is None else time.monotonic() + t
        while True:
            if not self._in_buf:                  # 注入优先：伪造帧先于后续真实字节
                inj = self._tap.drain_c2s_inject()
                if inj:
                    self._in_buf = inj
            if len(self._in_buf) >= n:
                out, self._in_buf = self._in_buf[:n], self._in_buf[n:]
                return out
            remain = None if deadline is None else deadline - time.monotonic()
            if remain is not None and remain <= 0:
                raise socket.timeout()
            slice_ = _READ_SLICE if (remain is None or remain > _READ_SLICE) else remain
            self._conn.settimeout(slice_)
            try:
                try:
                    chunk = self._conn.recv(n - len(self._in_buf))
                except socket.timeout:
                    continue                      # 分片超时：回顶部看有没有注入
            finally:
                try:
                    self._conn.settimeout(t)
                except OSError:
                    pass
            if not chunk:
                if self._in_buf:                  # 残余先交付，EOF 留到下次再报
                    out, self._in_buf = self._in_buf, b''
                    return out
                return b''                        # 真 EOF，如实上报
            chunk = self._filter_drop('C2S', chunk)
            if chunk:
                self._tap.feed('C2S', chunk)
                self._in_buf += chunk

    def settimeout(self, t):
        return self._conn.settimeout(t)

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    # ── 丢弃过滤器 ──
    def _filter_drop(self, direction, chunk):
        if self._tap.drop_armed(direction) <= 0:
            return chunk                          # 快路径：未布丢弃，零开销
        buf = self._hold[direction] + chunk
        self._hold[direction] = b''
        parts, rest = split_frames(direction, buf)
        if not parts:                             # 还没凑够一帧，继续攒
            self._hold[direction] = buf
            return b''
        self._tap.consume_armed(direction)        # 欠账 -1（只在这里消费）
        self._tap.note_dropped(direction, parts[0][1], parts[0][2])
        return b''.join(p[0] for p in parts[1:]) + rest


# ══════════════════════════════════════════════════════════ 抓包器 ══
class FrameTap:
    """会话抓包器：分帧 → 解码 → 环形缓冲 → 订阅推送 → 覆盖统计 → 世界模型。"""

    def __init__(self, protocol=None, ring_max=RING_MAX):
        self.proto = protocol or Protocol()
        self._lock = threading.RLock()
        # ★ 内核模块引用（2026-10-21）：注入帧要用**内核自己的** make_s2c 造，
        #   否则加密通道下注入的是「不压缩」的帧，客户端按原版流程先解压再取
        #   opcode → 收到的是废码（op=0）。内核 make_s2c 会按当前 _CRYPTO_MODE
        #   决定是否 LZSS 压缩，与真实下行帧保持一致。
        self._m4 = None
        self._ring = []
        self._seq = 0
        self._generation = 0                      # 每 clear() 一次 +1，供界面感知「别人清空了」
        self._ring_max = ring_max
        self._subs = []
        self._active_conn = None
        self._re = {}                             # 每方向独立分帧器，重连即弃
        self._inject_c2s = b''
        self._seen = {'C2S': {}, 'S2C': {}}
        self._unknown_op = set()                  # 内核日志报「未识别 op」的编码（累积）
        # ★ 轮次标记（2026-10-19）：`_unknown_op` 只增不减，调用方若想问「我刚发的
        #   那批里有没有新的未识别」，拿全量集是问不出来的——重试成功后旧标记还在。
        #   这里给每个 opcode 记一条「首次出现在第几轮」，配合 mark_round()/
        #   unknown_since() 就能只取增量；forget_unknown() 则用于撤销已证伪的误报。
        self._round = 0                           # 当前轮次号（mark_round 递增）
        self._unknown_round = {}                  # op -> 首次被报「未识别」的轮次
        self._drop_armed = {'C2S': 0, 'S2C': 0}   # 待执行的丢帧指令（消费即减）
        self._dropped = {'C2S': 0, 'S2C': 0}      # 已丢帧计数（统计用，只增不减）
        # ★ 环形缓冲淘汰计数（2026-10-20）：与 _dropped 是**两回事**，别混。
        #   _dropped   = 调试台**主动**丢帧（drop_next 指令），为了测客户端缺帧容错；
        #   _evicted   = 缓冲**被动**淘汰（超过 ring_max 的老帧被挤掉）。
        #   后者意味着「界面上看到的不是全部历史」——不显示出来，用户会以为
        #   帧数就这么多，从而漏判「这条链路其实很吵」。
        self._evicted = 0
        self._injected = {'C2S': 0, 'S2C': 0}
        self._mark_inject = {'C2S': 0, 'S2C': 0}  # 下一帧标记为「注入」的欠账
        self._desync = {'C2S': 0, 'S2C': 0}       # 分帧失步次数（跳字节重新对齐）
        self._raw_c2s = 0                         # 还需按「裸字节」消费的 C2S 字节数
        self._raw_buf = b''
        self._peak = 0
        # ★ 真实链路归因（2026-10-20）：GM 开战/胜利这类「一条命令连发一整个帧序列」
        #   的操作，界面要能一眼看出「这 N 帧是同一条真实链路产出的、顺序就是这样」。
        #   做法是**事后按 seq 区间回填**，而不是在发帧时打标：
        #   gm_war_start → _start_war 全程持 _lock 同步跑完，期间不会有别的线程插帧，
        #   所以「调用前的 last_seq() … 调用后的 last_seq()」正好圈住这条链路。
        #   在发帧时打标反而做不到——m4_server 不知道 tap 的存在（也不该知道）。
        self._marks = []                        # 链路段落：{lo, hi, label, kind}
        self._mark_kinds = {}                   # kind -> 累计段落数（统计用）
        self.installed = False
        self.world = {'mapid': None, 'mapname': '', 'minimap': None,
                      'x': None, 'y': None, 'npcs': [], 'in_war': False,
                      'war': self._empty_war()}

    # ── 连接生命周期 ──
    def new_connection(self):
        with self._lock:
            self._re = {}
            self._inject_c2s = b''
            self._raw_c2s = 0
            self._raw_buf = b''
            self._drop_armed = {'C2S': 0, 'S2C': 0}
            self._desync = {'C2S': 0, 'S2C': 0}
            self.world.update({'npcs': [], 'in_war': False,
                               'war': self._empty_war()})

    # ── 订阅 ──
    def subscribe(self, fn):
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def _notify(self, rec):
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(rec)
            except Exception:                     # noqa: BLE001
                pass

    # ── 核心：喂入字节 ──
    def feed(self, direction, data):
        """把字节喂进分帧器并产出帧记录。

        ★ 握手例外：Hello 之后客户端回的是**25 字节裸明文**，不是一个帧
          （M1 实测：`server_seed 混淆 3B + 22B 零填充`，见 hello_response）。
          若照帧解析，首字节会被当成长度 → 缓冲永久卡死 → 之后所有 C2S 帧都被吞。
          所以记录到 S2C Hello(op1) 时就把接下来的 25 个 C2S 字节标记为「裸区」。
        """
        if not data:
            return []
        out = []
        if direction == 'C2S':
            with self._lock:
                n_raw = self._raw_c2s
            if n_raw > 0:
                take = min(n_raw, len(data))
                self._raw_buf += data[:take]
                data = data[take:]
                with self._lock:
                    self._raw_c2s -= take
                    done = self._raw_c2s == 0
                    raw = self._raw_buf
                    if done:
                        self._raw_buf = b''
                if done:
                    out.append(self._record_raw(direction,
                                                '握手明文（非帧，25B）', raw))
                if not data:
                    return out
        with self._lock:
            re_ = self._re.get(direction)
            if re_ is None:
                re_ = self._re[direction] = Reassembler(direction)
        pairs, desync = re_.feed(data)
        if desync:
            with self._lock:
                self._desync[direction] += desync
            out.append(self._record_raw(direction,
                                        '分帧失步：跳过 %d 字节重新对齐' % desync,
                                        b''))
        out += [self._record(direction, op, body) for op, body in pairs]
        return out

    def _record_raw(self, direction, label, raw):
        """记录一段「不是一个帧」的字节（握手明文 / 失步对齐点）。"""
        now = time.time()
        with self._lock:
            self._seq += 1
            rec = {
                'seq': self._seq, 't': now,
                'ts': time.strftime('%H:%M:%S', time.localtime(now))
                      + '.%03d' % int(now % 1 * 1000),
                'dir': direction, 'op': -1, 'name': label, 'module': '',
                'known': True, 'src': 'raw', 'variants': 1,
                'size': len(raw), 'hex': ' '.join('%02X' % b for b in raw[:256]),
                'hex_len': len(raw),
                'rows': [{'i': 0, 'name': '原始字节', 'kind': 'R', 'size': str(len(raw)),
                          'value': raw.hex(), 'disp': '%dB 非帧数据' % len(raw),
                          'hex': ' '.join('%02X' % b for b in raw[:32])}],
                'tail': '', 'tail_len': 0, 'fit': True, 'layout': 0, 'n_layouts': 0,
                'note': '这不是协议帧，是握手/对齐产生的原始字节',
                'summary': label,
                'dropped': False, 'injected': False, 'impl': True,
            }
            self._push(rec)
        self._notify(rec)
        return rec

    def _build_rec(self, direction, op, body, dropped=False):
        info = self.proto.describe(direction, op, body)
        dec = decode_best(info['layouts'], body)
        now = time.time()
        hyp = info['hint']
        note = dec['note']
        if hyp:
            note = (note + '；' if note else '') + hyp
        return {
            'seq': 0,
            't': now,
            'ts': time.strftime('%H:%M:%S', time.localtime(now))
                  + '.%03d' % int(now % 1 * 1000),
            'dir': direction,
            'op': op,
            'name': info['name'] or ('op%d' % op),
            'module': info['module'],
            'known': info['known'],
            'src': info['src'],                   # overlay / dict / none
            'variants': info['variants'],
            'size': len(body),
            'hex': ' '.join('%02X' % b for b in body[:256]),
            'hex_len': len(body),
            'rows': dec['rows'],
            'tail': (' '.join('%02X' % b for b in dec['tail'][:64])
                     + (' …(共 %dB)' % dec['tail_len'] if dec['tail_len'] > 64 else '')),
            'tail_len': dec['tail_len'],
            'fit': dec['fit'],
            'layout': dec['layout'],
            'n_layouts': dec['n_layouts'],
            'note': note,
            'summary': self._summary(dec['rows'], body),
            'dropped': dropped,
            'injected': False,
            # 是否属于某条「真实链路」段落（GM 开战/胜利…）：事后由 mark_range 回填
            'warcmd': False,
            # 服务端未实现：由内核日志 `（未识别 op=N）` 旁听得出
            'impl': (op not in self._unknown_op) if direction == 'C2S' else True,
        }

    def _record(self, direction, op, body):
        rec = self._build_rec(direction, op, body)
        with self._lock:
            self._seq += 1
            rec['seq'] = self._seq
            if self._mark_inject[direction] > 0:
                self._mark_inject[direction] -= 1
                rec['injected'] = True
            self._seen[direction][op] = self._seen[direction].get(op, 0) + 1
            if direction == 'S2C' and op == 1:
                # Hello 之后客户端回 25B 裸明文（见 feed 的说明），提前标记裸区
                self._raw_c2s = 25
                self._raw_buf = b''
            self._push(rec)
        self._update_world(direction, op, body, rec)
        self._notify(rec)
        return rec

    def note_dropped(self, direction, op, body):
        """记录一帧「被本工具吞掉」——客户端/内核看不到它，但调试台要看到。

        注意：只增 `_dropped`（统计），**不碰** `_drop_armed`（待执行指令）。
        两者混用会让一次丢帧指令变成永久丢帧（这里踩过）。
        """
        rec = self._build_rec(direction, op, body, dropped=True)
        rec['summary'] = '⛔ 已丢弃 · ' + rec['summary']
        with self._lock:
            self._seq += 1
            rec['seq'] = self._seq
            self._dropped[direction] += 1
            self._push(rec)
        self._notify(rec)
        return rec

    def _push(self, rec):
        """调用方需持锁。"""
        self._ring.append(rec)
        if len(self._ring) > self._ring_max:
            _over = len(self._ring) - self._ring_max
            del self._ring[:_over]
            # ★ 淘汰计数（2026-10-20）：环满后每挤掉一帧 +1。
            #   不计数的话，「界面上只剩最近 ring_max 帧」这件事对用户完全不可见。
            self._evicted += _over
        self._peak = max(self._peak, len(self._ring))

    @staticmethod
    def _summary(rows, body):
        """列表行上的一眼摘要：优先字符串字段，其次前几个数值。"""
        strs = [r['value'] for r in rows
                if r.get('kind') in ('S', 'V') and isinstance(r.get('value'), str)
                and r['value'].strip()]
        if strs:
            s = ' / '.join(strs)
        else:
            # ★ 列表字段优先（2026-10-20）：op105 的 entries 里装着宠物名，
            #   若不看 L，摘要会退化成 header 的 '1, 1, 1'，看不出这是「随行宠物」帧。
            #   取条目里的字符串值（name/title），没有字符串才退回计数。
            _loop_strs = []
            _loop_n = None
            for r in rows:
                if r.get('kind') != 'L' or not isinstance(r.get('value'), list):
                    continue
                if r.get('name', '').endswith(']'):
                    continue          # 跳过逐条展开行，只看汇总行
                _loop_n = len(r['value'])
                for e in r['value']:
                    if not isinstance(e, dict):
                        continue
                    for _v in e.values():
                        if isinstance(_v, str) and _v.strip():
                            _loop_strs.append(_v.strip())
            if _loop_strs:
                s = ' / '.join(_loop_strs)
            elif _loop_n is not None:
                s = '×%d' % _loop_n
            else:
                s = ', '.join(str(r['value']) for r in rows
                              if r.get('kind') == 'I'
                              and isinstance(r.get('value'), int))
        if not s:
            return '%dB' % len(body)
        return (s[:48] + '…') if len(s) > 48 else s

    # ── 世界模型 ──
    @staticmethod
    def _named(rec):
        """把已解码的字段取成 {名字: 值}。

        世界模型**复用解码结果**，不重复手搓 struct：布局改一次就全局一致，
        也不会出现「界面显示的坐标」与「地图上画的坐标」两套解析各说各话。
        """
        return {r['name']: r['value'] for r in rec.get('rows') or []
                if r.get('name') and isinstance(r.get('value'), (int, str))}

    def _update_world(self, direction, op, body, rec):
        if direction != 'S2C':
            return
        w = self.world
        v = self._named(rec)
        try:
            if op == 96:                                  # SceneM：切图 + ClearAllObject
                if {'mapid', 'minimap', 'x', 'y'} <= set(v):
                    w['minimap'], w['mapid'] = v['minimap'], v['mapid']
                    w['x'], w['y'] = v['x'], v['y']
                    w['mapname'] = v.get('name', '')
                    w['npcs'] = []
            elif op == 97 and {'x', 'y'} <= set(v):        # GotoM：定位
                w['x'], w['y'] = v['x'], v['y']
            elif op == 86 and {'x', 'y'} <= set(v):        # MapTrack：移动
                w['x'], w['y'] = v['x'], v['y']
            elif op == 99 and {'x', 'y'} <= set(v):        # MapAddNpc
                w['npcs'].append({'id': v.get('id'), 'shape': v.get('shape'),
                                  'x': v['x'], 'y': v['y'],
                                  'name': (v.get('name') or '').strip()
                                          or ('NPC %s' % v.get('id'))})
                del w['npcs'][:max(0, len(w['npcs']) - 200)]
            else:
                self._update_war(w, op, v, rec)
        except (TypeError, ValueError):
            pass                               # 模型只是辅助视图，解不出就跳过

    # ── 战斗模型 ──
    # 把 War* 那十来种帧翻译成「谁在场、谁打了谁、谁掉了多少血、谁死了」。
    # 通用帧流里这些帧只是一串 opcode，战斗一开打就淹在几十条记录里看不出回合语义。
    @staticmethod
    def _empty_war():
        return {'active': False, 'round': 0, 'units': {}, 'log': [],
                'scene': None, 'alineup': None, 'elineup': None, 'wtype': None}

    @staticmethod
    def _uname(war, wid):
        u = war['units'].get(str(wid))
        return (u.get('name') or '单位') if u else ('单位%s' % wid)

    @staticmethod
    def _war_note(w, seq, kind, text, **extra):
        e = {'seq': seq, 'kind': kind, 'text': text}
        e.update(extra)
        war = w['war']
        war['log'].append(e)
        del war['log'][:max(0, len(war['log']) - 40)]

    def _update_war(self, w, op, v, rec):
        war = w['war']
        seq = rec.get('seq')
        if op == 112:                                     # WarStart
            w['war'] = war = self._empty_war()
            war['active'] = w['in_war'] = True
            war['scene'] = v.get('scene')
            war['alineup'], war['elineup'] = v.get('alineup'), v.get('elineup')
            war['wtype'] = v.get('wtype')
            self._war_note(w, seq, 'start',
                           '战斗开始 · 场景 %s · 我方 %s vs 敌方 %s · 类型 %s'
                           % (v.get('scene'), v.get('alineup'),
                              v.get('elineup'), v.get('wtype')))
        elif op == 113:                                   # WarEnd
            w['in_war'] = False
            war['active'] = False
            alive = sum(1 for u in war['units'].values() if not u['dead'])
            self._war_note(w, seq, 'end',
                           '战斗结束 · 共 %d 回合 · 仍在场 %d/%d'
                           % (war['round'], alive, len(war['units'])))
        elif op == 114:                                   # WarTurn
            war['round'] += 1
            self._war_note(w, seq, 'turn', '第 %d 回合' % war['round'])
        elif op == 115 and 'id' in v:                     # WarAdd
            wid = v['id']
            war['units'][str(wid)] = {
                'id': wid, 'name': (v.get('name') or '').strip() or ('单位%d' % wid),
                'shape': v.get('shape'), 'allied': bool(v.get('allied')),
                'cls': v.get('cls'), 'grade': v.get('grade'),
                'dead': False, 'dmg': 0, 'heal': 0, 'hits': 0, 'hp_pct': None,
                'last_bf': None, 'last_chg': None,
                'last_hurt': None, 'last_life': None, 'last_ward': None}
            self._war_note(w, seq, 'add', '%s(id=%d) 加入 · %s'
                           % (self._uname(war, wid), wid,
                              '我方' if v.get('allied') else '敌方'), id=wid)
        elif op == 116 and 'id' in v:                     # WarDel
            u = war['units'].pop(str(v['id']), None)
            self._war_note(w, seq, 'del', '%s(id=%s) 离场'
                           % (u['name'] if u else '单位%s' % v['id'], v['id']))
        elif op == 117 and {'att_id', 'vic_id'} <= set(v):  # WarAttackNormal
            self._war_note(w, seq, 'attack', '普通攻击：%s → %s'
                           % (self._uname(war, v['att_id']),
                              self._uname(war, v['vic_id'])),
                           att=v['att_id'], vic=v['vic_id'])
        elif op == 119 and 'id' in v:                     # WarAttackNormalEnd
            self._war_note(w, seq, 'swing_end', '%s 收招'
                           % self._uname(war, v['id']), id=v['id'])
        elif op == 122 and 'id' in v:                     # WarPerform
            vics = [r['value'] for r in rec.get('rows') or []
                    if r.get('name') == 'vic_ids' and isinstance(r.get('value'), int)]
            self._war_note(w, seq, 'perform',
                           '%s 出手 · 招式 pid=%s%s'
                           % (self._uname(war, v['id']), v.get('pid'),
                              (' → ' + '、'.join(self._uname(war, x) for x in vics))
                              if vics else ''),
                           id=v['id'], pid=v.get('pid'), vics=vics)
        elif op == 123 and 'hurt_life_ward' in v:         # WarWState ★ 核心
            bf = v['hurt_life_ward']
            chg = v.get('hp_chg')
            # ★ 位域是 MSB-first 解包（客户端 `UnpackBit(off)` 的 off=0 是最高位），
            #   内核 `m4_server.build_war_wstate` 发的是 `(hurt<<4)|(life<<2)|ward`。
            #   曾经按 LSB 拆（bf&0xF 当 hurt），把 hurt=1 life=0 ward=1 读成
            #   hurt=1 life=1 ward=0 —— 而「用调试台核对敌方是不是 ward=1」正是
            #   这套工具的主用途，读反了会得到相反结论。
            hurt, life, ward = (bf >> 4) & 0xF, (bf >> 2) & 0x3, bf & 0x3
            u = war['units'].get(str(v.get('id')))
            if u is not None:
                u['last_bf'], u['last_chg'] = bf, chg
                # 拆好的三项一并存下：界面只显示，不再自己拆一遍
                # （拆两遍就有两个真相，改了一处忘另一处就是这次的教训）
                u['last_hurt'], u['last_life'], u['last_ward'] = hurt, life, ward
                if isinstance(chg, int):
                    if chg > 0:
                        u['dmg'] += chg
                    elif chg < 0:
                        u['heal'] += -chg
                if life == 2:
                    u['dead'] = True
                u['hits'] += 1
            # ★ 只报事实：原始 hp_chg 与位域都摆出来，符号含义按本服约定标注。
            #   若哪天客户端演成「回血」，在这里一眼就能看出是符号约定反了。
            sign = ('扣 %d' % chg) if (isinstance(chg, int) and chg > 0) else (
                ('回 %d' % -chg) if (isinstance(chg, int) and chg < 0) else '—')
            self._war_note(w, seq, 'wstate',
                           '%s hp_chg=%s(%s) · hurt=%d life=%d%s ward=%d'
                           % (self._uname(war, v.get('id')), chg, sign,
                              hurt, life, ' 死亡' if life == 2 else '', ward),
                           id=v.get('id'), hp_chg=chg, hurt=hurt, life=life, ward=ward)
        elif op == 125 and 'id' in v:                     # WarHPPercent
            u = war['units'].get(str(v.get('id')))
            if u is not None:
                u['hp_pct'] = v.get('per')
            self._war_note(w, seq, 'hp', '%s 血条 %s%%'
                           % (self._uname(war, v.get('id')), v.get('per')),
                           id=v.get('id'), per=v.get('per'))

    # ── 读取 ──
    def frames(self, since=0, limit=0):
        with self._lock:
            out = [r for r in self._ring if r['seq'] > since]
            if limit and len(out) > limit:
                out = out[-limit:]
            return out

    # ── 真实链路归因：把一段 seq 区间标成「一条链路」 ──
    #
    # 为什么是「事后按区间回填」而不是「发帧时打标」：
    #   ① m4_server 不认识 tap（也不该认识：FakeConn 测试要能裸跑）；
    #   ② 回填天然兜住**整条链路**——含 _resync_scene 的 SceneM/AddNpc/GotoM、
    #      掉落 ItemAdd 这些「看起来不像战斗帧」的部分。界面上标出来正好回答
    #      「打赢之后到底还发了什么、什么顺序」这个最常被问的问题。
    #
    # 调用方只需三行：
    #     lo = tap.last_seq();  r = srv.gm_war_win();  tap.mark_range(lo, tap.last_seq(), 'GM 胜利', kind='war_win')
    def mark_range(self, lo, hi, label, kind='chain'):
        """把 seq ∈ [lo, hi] 的帧标成同一条链路（事后回填，返回实际命中帧数）。

        为什么只存区间、不往每帧里写字段：这里每 0.12s 就要把新帧序列化推给浏览器，
        多存一份长串在几千帧上纯属浪费；而一局里的链路段落只有个位数。
        所以帧记录保持 `warcmd` 布尔位（供列表着色），文案与区间另存 `_marks`。

        lo/hi 用调用前后的 `last_seq()` 即可；lo > hi（这次没发帧）视为无段落。
        """
        lo, hi = int(lo), int(hi)
        if hi < lo:
            return 0
        label = str(label or '')
        with self._lock:
            # ★ 返回的是「本次真正标上的帧数」，而段落记的 frames 也是它 ——
            #   不能拿 hi-lo+1 当帧数：环形缓冲可能已经淘汰掉了老帧、
            #   或者这个区间里有部分帧早被别的段落标过。
            #   两者都对不上时，界面上的「#lo-hi（N 帧）」就会骗人。
            n = 0
            for r in self._ring:
                if lo <= r['seq'] <= hi:
                    if not r.get('warcmd'):
                        n += 1
                    r['warcmd'] = True
            self._marks.append({'lo': lo, 'hi': hi, 'label': label,
                                'kind': kind, 'frames': n,
                                'ts': time.strftime('%H:%M:%S')})
            # 段落只留最近 40 条：一局几十场战斗，再往上翻已无意义
            if len(self._marks) > 40:
                del self._marks[:-40]
            self._mark_kinds[kind] = self._mark_kinds.get(kind, 0) + 1
        return n

    def marks(self):
        """已标记的链路段落（新→旧）。"""
        with self._lock:
            return [dict(x) for x in self._marks]

    def mark_of(self, seq):
        """某帧属于哪条链路（不属于返回 None）。区间段落只有个位数，线性找足够。"""
        with self._lock:
            for x in reversed(self._marks):
                if x['lo'] <= seq <= x['hi']:
                    return dict(x)
            return None

    def last_seq(self):
        with self._lock:
            return self._seq

    def clear(self):
        """清空会话缓冲。

        `_seq` 一并归零（否则 `stats()['frames']` 清空后仍显示旧计数），
        同时 `_generation` +1：序号会从头复用，界面必须靠这个代数变化去重置自己
        缓存的帧表，否则新帧会被 `byId` 当成「已见过」而丢掉。多标签同时开着时，
        另一个标签清空也能被正确感知。
        """
        with self._lock:
            self._ring = []
            self._seq = 0
            self._generation += 1
            self._seen = {'C2S': {}, 'S2C': {}}
            self._unknown_op = set()
            self._unknown_round = {}
            self._drop_armed = {'C2S': 0, 'S2C': 0}
            self._dropped = {'C2S': 0, 'S2C': 0}
            self._evicted = 0
            self._injected = {'C2S': 0, 'S2C': 0}
            self._mark_inject = {'C2S': 0, 'S2C': 0}
            # 段落是「本次会话里哪几段是真实链路」的记录，清空会话时一并归零
            self._marks = []
            self._mark_kinds = {}
            self._desync = {'C2S': 0, 'S2C': 0}
            self._re = {}
            self._raw_c2s = 0
            self._raw_buf = b''
            self.world.update({'mapid': None, 'mapname': '', 'minimap': None,
                               'x': None, 'y': None, 'npcs': [],
                               'in_war': False, 'war': self._empty_war()})

    # ── 内核日志旁听 ──
    def note_log(self, line):
        """`m4_server.log` 被旁路到这里，从措辞里认出「服务端尚未实现」的编码。

        目前内核有两处这样的日志，都要认：
          `（未识别 op=N）`                        —— 非战斗下的兜底
          `⚠ 未做逻辑的战斗指令 op=N → 保底回 WarTurn` —— 战斗中专有的兜底
        """
        for pat in (r'未识别 op=(\d+)', r'未做逻辑的战斗指令 op=(\d+)'):
            m = re.search(pat, line)
            if m:
                op = int(m.group(1))
                with self._lock:
                    self._unknown_op.add(op)
                    # 只记**首次**出现的那一轮：同一 op 重复报错不覆盖，否则
                    # 「我这轮又发了一次还是失败」会被记成更晚的轮次，掩盖首次证据。
                    self._unknown_round.setdefault(op, self._round)
                return

    # ── 轮次标记（供巡检/测试问「这一轮有没有新的未识别」）──
    def mark_round(self):
        """开一轮：返回递增后的轮次号。

        典型用法（coverage_smoke 的探针段）::

            r = tap.mark_round()
            ... 发一批探针 ...
            newly = tap.unknown_since(r)     # 只有这批里新冒出来的
            tap.forget_unknown(op)           # 已证伪的误报，撤销标记

        轮次与 `stats()['generation']` 是两回事：generation 表示「缓冲被清空过」，
        round 表示「调用方又发了一批帧」。清空不会重置轮次（历史证据仍有效）。
        """
        with self._lock:
            self._round += 1
            return self._round

    def current_round(self):
        """当前轮次号（只读）。"""
        with self._lock:
            return self._round

    def unknown_since(self, round_no, cumulative=False):
        """返回「在第 round_no 轮及之后首次出现」的未识别 opcode 集合。

        cumulative=False（默认）只返回**恰好在** round_no 那一轮首次出现的；
        传 True 则包含之后各轮新出现的（用于「从这里开始的整段」）。
        """
        with self._lock:
            if cumulative:
                return {op for op, r in self._unknown_round.items() if r >= round_no}
            return {op for op, r in self._unknown_round.items() if r == round_no}

    def unknown_round_of(self, op):
        """某 opcode 首次被报「未识别」的轮次；没报过返回 None。"""
        with self._lock:
            return self._unknown_round.get(op)

    def forget_unknown(self, op):
        """撤销一个「未识别」标记（已证伪的误报）。

        只该在**确认是探针 body 问题**时调用：例如重试加长后内核不再报未识别。
        它同时清 `_unknown_op`（喂给 stats/界面的集合）与 `_unknown_round`，
        否则历史集会一直把已修好的 opcode 当成缺口。返回是否真的撤销了。
        """
        op = int(op)
        with self._lock:
            had = op in self._unknown_op
            self._unknown_op.discard(op)
            self._unknown_round.pop(op, None)
            return had

    # ── 统计 / 覆盖 ──
    def stats(self, include_tables=True):
        """会话统计。

        `include_tables=False` 会省掉两张全表（约 300 行）——SSE 心跳走这条：
        表格是静态的，让界面开机拉一次再自己合并计数，比每 2 秒推 40KB 强得多。
        表长度（分母）无论哪种模式都保留。
        """
        with self._lock:
            seen = {d: dict(v) for d, v in self._seen.items()}
            unknown = set(self._unknown_op)
            unknown_round = dict(self._unknown_round)
            snap = {'frames': self._seq, 'peak': self._peak,
                    'evicted': self._evicted,
                    'generation': self._generation,
                    'round': self._round,
                    'dropped': dict(self._dropped),
                    'armed': dict(self._drop_armed),
                    'desync': dict(self._desync),
                    'injected': dict(self._injected),
                    # 链路段落（新→旧，最多 40 条）：界面据此把 GM 开战/胜利那几段标红
                    'marks': [dict(x) for x in self._marks],
                    'mark_kinds': dict(self._mark_kinds),
                    'world': json.loads(json.dumps(self.world, ensure_ascii=False))}
        c2s_seen, s2c_seen = set(seen['C2S']), set(seen['S2C'])
        c2s_all = self.proto.table('C2S')
        s2c_all = self.proto.table('S2C')
        totals = {'C2S': len(c2s_all), 'S2C': len(s2c_all)}
        # ★ 分母对账：「客户端实际能发」与「字典有字段定义」是两个口径，差异摊开不藏
        space = set(self.proto.c2s_upspace)
        dictops = set(self.proto.c2s_variants)
        c2s_nodef = sorted(space - dictops)      # 会发，但连字段定义都没有 → 优先逆向
        c2s_orphan = sorted(dictops - space)     # 字典有，客户端找不到常量 → 多为幻影
        for row in c2s_all:
            row['count'] = seen['C2S'].get(row['op'], 0)
            row['impl'] = row['op'] not in unknown
        for row in s2c_all:
            row['count'] = seen['S2C'].get(row['op'], 0)
        # ★ 会话里出现过、字典却没有登记的编码，必须补进表里。
        #   这些恰恰是「完善游戏」最该先看的东西，藏起来等于把线索丢了。
        unknown_c2s_seen = c2s_seen - {r['op'] for r in c2s_all}
        unknown_s2c_seen = s2c_seen - {r['op'] for r in s2c_all}
        c2s_all += [{'op': op, 'name': '（字典未登记）', 'module': '', 'fields': 0,
                     'variants': 0, 'src': 'none', 'count': seen['C2S'][op],
                     'impl': op not in unknown} for op in sorted(unknown_c2s_seen)]
        s2c_all += [{'op': op, 'name': '（字典未登记）', 'module': '', 'fields': 0,
                     'variants': 0, 'src': 'none', 'count': seen['S2C'][op],
                     'impl': True} for op in sorted(unknown_s2c_seen)]
        c2s_all.sort(key=lambda r: r['op'])
        s2c_all.sort(key=lambda r: r['op'])
        out = {
            'proto_ok': self.proto.ok, 'proto_error': self.proto.error,
            'frames': snap['frames'], 'ring': snap['peak'], 'ring_max': self._ring_max,
            'generation': snap['generation'],
            # 当前轮次号（mark_round 递增）。与 generation 不同：后者表示缓冲被清空，
            # 前者表示调用方又发了一批帧——巡检靠它做「这批里有没有新缺口」的增量判定。
            'round': snap['round'],
            'seen': {'C2S': len(c2s_seen), 'S2C': len(s2c_seen)},
            'total_ops': totals,
            'overlay_ops': {'S2C': sum(1 for r in s2c_all if r['src'] == 'overlay')},
            'dropped': snap['dropped'], 'armed': snap['armed'],
            # 环形缓冲淘汰数：>0 表示界面上的帧列表不是全部历史
            'evicted': snap['evicted'],
            'ring_full': snap['evicted'] > 0,
            'injected': snap['injected'], 'desync': snap['desync'],
            'marks': snap['marks'],
            'mark_kinds': snap['mark_kinds'],
            'unknown_c2s': sorted(unknown),
            # 每个未识别编码「首次出现在第几轮」。调用方据此只取自己那一批的增量
            # （见 mark_round / unknown_since），不必再靠日志文件偏移去猜。
            'unknown_c2s_round': {str(op): r for op, r in unknown_round.items()},
            # 服务端发了、但客户端没有路由的 S2C。
            # 客户端 net.py 的 OnServerCmd 会打印 `OnServerCmd: 0xNN` 与
            # `!!!! func==None, OnServerCmd: 0xNN` 两行，然后 return —— **不崩、不弹框**。
            # 所以它不是故障，而是「服务端在自嗨」：看着像有应答，客户端其实什么也没做。
            # 实例：op 250。客户端 `C2GS_PING = 250` 是个定义了从不使用的死常量，
            # 服务端却拿它当 PING 回显的 opcode（当链路探针够用，别指望客户端有反应）。
            'unrouted_s2c': sorted(unknown_s2c_seen),
            'undict_c2s': sorted(unknown_c2s_seen),      # 出现过且字典没登记
            # 分母来源与差异（覆盖率百分比建立在哪个分母上，一眼可见）
            'space': {'C2S': len(space)},                # 客户端上行常量全集
            'c2s_nodef': c2s_nodef,                      # 会发但没有字段定义
            'c2s_orphan': c2s_orphan,                    # 字典有但客户端没有
            'world': snap['world'],
            # 「op → 出现次数」始终带上：界面据此把计数合进自己那份静态表，
            # 于是 SSE 心跳不必每 2 秒重推 300 行表格。
            'counts': {'C2S': {str(op): n for op, n in seen['C2S'].items()},
                       'S2C': {str(op): n for op, n in seen['S2C'].items()}},
        }
        if include_tables:
            out['c2s_table'] = c2s_all
            out['s2c_table'] = s2c_all
        return out

    # ── 注入 / 丢弃 ──
    def inject(self, direction, op, payload=b''):
        """注入一帧。返回 (是否成功, 说明)。

        S2C：立刻写回客户端（测客户端对某帧的反应）。
        C2S：塞进缓冲，内核下次 recv 时把它当成「客户端发来的」读走
             （免点 GUI 复现操作，例如伪造一次 NPC 对话选择）。
        """
        with self._lock:
            conn = self._active_conn
            if conn is None:
                return False, '当前没有已连接的客户端'
            if direction == 'C2S':
                wire = make_c2s(op, payload)
                self._inject_c2s += wire
                self._injected['C2S'] += 1
                self._mark_inject['C2S'] += 1
                return True, '已排入上行 op=%d（%dB），内核下次 recv 读走' % (op, len(wire))
            self._mark_inject['S2C'] += 1            # 让这一帧在抓包里标成「注入」
        try:
            # ★ 用内核的 make_s2c：加密通道下它会先 LZSS 压缩（与真实下行帧一致），
            #   tap 自带的 make_s2c 不压缩，注入的帧客户端解出来是废码。
            wire = (self._m4.make_s2c(op, payload) if self._m4 is not None
                    else make_s2c(op, payload))
            conn.sendall(wire)                      # 走 TappedConn → 自动进抓包
        except (OSError, ConnectionError) as e:
            with self._lock:
                self._mark_inject['S2C'] = max(0, self._mark_inject['S2C'] - 1)
            return False, '注入失败：%r' % e
        with self._lock:
            self._injected['S2C'] += 1
        return True, '已注入下行 op=%d' % op

    def drain_c2s_inject(self):
        """内核 recv 前调用：兑现伪造的上行帧。"""
        with self._lock:
            out, self._inject_c2s = self._inject_c2s, b''
        if out:
            self.feed('C2S', out)                 # 伪造帧与真实收发并列进抓包
        return out

    def dropped(self, direction):
        """已吞掉的帧数（统计，只增不减）。"""
        with self._lock:
            return self._dropped.get(direction, 0)

    def drop_armed(self, direction):
        """还欠几次丢帧（待执行指令）。"""
        with self._lock:
            return self._drop_armed.get(direction, 0)

    def consume_armed(self, direction):
        """扣掉一次待执行的丢帧指令，返回扣之前是否还有欠账。"""
        with self._lock:
            n = self._drop_armed.get(direction, 0)
            if n > 0:
                self._drop_armed[direction] = n - 1
            return n > 0

    def drop_next(self, direction):
        """让下一帧在指定方向被吞掉（测缺帧 / 超时容错）。返回累计欠账数。"""
        with self._lock:
            self._drop_armed[direction] = self._drop_armed.get(direction, 0) + 1
            return self._drop_armed[direction]

    # ── 落地 ──
    def export_jsonl(self, path, only=None):
        """导出会话为 JSONL（每行一帧，含解码字段）—— 可回放、可进版本库。"""
        with self._lock:
            recs = list(self._ring)
        n = 0
        with io.open(path, 'w', encoding='utf-8') as f:
            for r in recs:
                if only and r['dir'] not in only:
                    continue
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
                n += 1
        return n

    # ── 安装 ──
    def install(self, m4_module):
        """把抓包挂到内核上：包装 conn + 旁听 log。

        只替换 `M4Server.handle` 与 `M4Server.log` 两个方法：不碰 `m4_server.py` 的
        （约 140 处）sendall，也不碰 `recv_c2s`。重复调用幂等。
        """
        cls = m4_module.M4Server
        if getattr(cls, '_tap_installed', False):
            return False
        tap = self
        self._m4 = m4_module
        orig_handle = cls.handle
        orig_log = cls.log

        def handle(self_, conn, *a, **kw):
            wrapped = TappedConn(conn, tap)
            tap.new_connection()
            with tap._lock:
                tap._active_conn = wrapped
            try:
                return orig_handle(self_, wrapped, *a, **kw)
            finally:
                with tap._lock:
                    tap._active_conn = None

        def log(self_, s):
            try:
                tap.note_log(s)
            except Exception:                     # noqa: BLE001
                pass
            return orig_log(self_, s)

        cls.handle = handle
        cls.log = log
        cls._tap_installed = True
        self.installed = True
        return True
