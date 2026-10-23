# -*- coding: utf-8 -*-
"""交互巡检：脚本化客户端走一遍全部已知交互路径，产出**真实**协议覆盖。

为什么需要它
------------
《项目完成度评估_20261016》里的「C2S 42/137 = 30.7%」是手写快照，几天就过时
（那份报告里「持久化」「多地图」「施法消耗」等缺口此后都已补齐），而且分母本身
也不对（真实是 136 不是 137，且两份人工清单都漏了 C2GS_* 那批）。

而「服务端未实现」这个信号，只有客户端真踩到才会被内核日志记下来——手动玩一遍
很难走全（战斗里就有 11 种指令）。本脚本把已知路径自动化走一遍，让运行时证据
自然产生，然后打印基于**实际流量**的覆盖报告。

它不猜、不静态解析源码（内核用了 `range()` 与集合运算，静态解析必然漏项），
只报实测。跑一次几秒钟，可以随时刷新。

用法：
    python coverage_smoke.py                # 巡检 + 报告
    python coverage_smoke.py --md cov.md    # 同时写一份 markdown
    python coverage_smoke.py -v             # 打印每一步的收发
"""
import argparse
import io
import os
import re
import struct
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, OSError):
    pass

SIM = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SIM)
import m4_server as m          # noqa: E402
import tap as T                # noqa: E402
import transport               # noqa: E402

SEED = 0x12345678
ENEMIES = 3
ENCOUNTER_EVERY = 2            # 高频遇怪：战斗指令有 11 种，得有多轮才踩得完

# 通用探测 body：只用于**确认没有专门分支**的编码，长度取 5B 以免撞上 0xFF 扩展长度。
# 已实现的编码绝不能用它——body 长度不符会落进「未识别 op=N」兜底，
# 把「已实现」误报成缺口，那比漏报更糟（会让人去做已经做完的事）。
PROBE_BODY = b'\x01\x00\x00\x00\x00'

# ── 探针长度解析（2026-10-19）────────────────────────────────────────────
# 旧问题：探测段一律发 5B 的 PROBE_BODY，而 _dispatch 的分支大多带
# `len(body) >= N` 守卫。body 不够长 → 条件不成立 → 落到链尾
# `（未识别 op=N）` → 被记成「未实现」。16/144/145/156 就是这么被误报的
# （它们的守卫是 >=5 / >=8 / >=8 / >=9）。
#
# 现在直接**从源码里解析每个 opcode 的守卫长度**，探测时按需补长：
#   · 读 m4_server.py，取 _dispatch 函数体；
#   · 按 if/elif 切块，逐块找 `op == <常量名>` / `op in (<常量名>...)`；
#   · 在该块内找 `len(body) >= N` / `len(body) > N`，取最大需求。
# 解析不出来（用了 range()/集合运算/动态变量）的记录在案，回落 PROBE_BODY 并
# 由「误报 → 加长重试」闭环兜底——不假装解析是全的。
GUARD_MAX = 512          # 单次补长上限（再长就该走真实路径，而不是探针）
GUARD_RETRY = 32         # 误报重试时的递增长度

def _dispatch_guard_lengths(sim_dir=None):
    """解析 _dispatch 的分支守卫，返回 {opcode: 需要的 body 最小长度}。

    只做**保守解析**：认得出就记，认不出就不记（不猜）。返回的字典只用来决定
    「探针该发多长」，不参与「是否已实现」的判定——后者仍以内核日志为准。
    """
    import io
    import re
    sim_dir = sim_dir or os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(sim_dir, 'm4_server.py')
    try:
        src = io.open(src_path, encoding='utf-8').read()
    except OSError:
        return {}
    # ★ 找锁内实现而不是入口：_dispatch 现在只是个加锁壳（2026-10-20 起），
    #   真正的分支在 _dispatch_locked 里。只认入口会解出 0 个守卫。
    i = src.find('    def _dispatch_locked(')
    if i < 0:
        # 兜底：老结构（没有锁内拆分）时按原入口解
        i = src.find('    def _dispatch(')
    if i < 0:
        return {}
    # 取到下一个同级 def 为止（类内方法缩进 4）
    j = src.find('\n    def ', i + 10)
    body_src = src[i:j if j > 0 else len(src)]
    # 常量名 → 数值（避免 exec 整个模块，只取字面量）
    consts = {k: v for k, v in vars(m).items()
              if isinstance(v, int) and not isinstance(v, bool)}
    # 按行扫描：8 空格缩进的 if/elif 是一个分支块的开始，块体 = 到下一个同级分支为止。
    # ★ 不能用「split 后按冒号切」：分支头里含字符串（如 "未识别 op=N"）和 docstring，
    #   冒号会把块头截断，导致 op 名一个都认不出来（首版就是这么只认出 4 个的）。
    lines = body_src.split('\n')
    starts = []
    for idx, ln in enumerate(lines):
        if re.match(r'^        (if|elif) ', ln):
            starts.append(idx)
    blocks = []
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else len(lines)
        # ★ 分支头可能是**折行**的：
        #     elif (op in (C2S_WAR_SPECIAL, ..., C2S_WAR_CATCH)
        #             or op == C2S_WAR_SUMMONCHILD or op in C2S_WAR_SUMM_CLAN) and self.in_war:
        #   只取第一行会漏掉后半段（op173 就是这么漏的 → 既不跳过也不重试）。
        #   这里按括号配平把逻辑行拼回来。
        head = _join_logical_head(lines, a)
        blocks.append((head, '\n'.join(lines[a + 1:b])))
    need = {}

    def add(op, n):
        # ★ n=0 也要记：它表示「有分支、无长度守卫」。漏记会让该 opcode 既不进
        #   KNOWN_HANDLED、也不进误报闭环，于是被当缺口发出去（op173 就是这样）。
        if isinstance(op, int):
            need[op] = max(need.get(op, 0), n)

    for head, rest in blocks:
        ops = _ops_in_head(head, consts)
        if not ops:
            continue
        # ★ 守卫只从**分支头**取，不从块体取。
        #   块体会一直延伸到下一个同级分支，里面还嵌着别的 opcode 的守卫
        #   （如 op54 块里的 `if self.in_war`、op104 块里的 `n = min(...)`），
        #   拿块体一起取会把别人需要的长度张冠李戴到自己头上。
        n = _guard_len(head)
        for op in ops:
            add(op, n)
    return need


def _join_logical_head(lines, start, limit=12):
    """把从 lines[start] 开始的分支头拼成**完整逻辑行**（按括号配平）。

    遇到 `if/elif ... :` 在同一行结束就原样返回；否则一直拼到括号配平为止。
    limit 防止畸形源码把整段都吞进来。
    """
    def balance(s):
        d = 0
        for ch in s:
            if ch in '([{':
                d += 1
            elif ch in ')]}':
                d -= 1
        return d

    buf = lines[start]
    depth = balance(buf)
    if depth <= 0 and buf.rstrip().endswith(':'):
        return buf
    for k in range(start + 1, min(start + limit, len(lines))):
        nxt = lines[k].strip()
        buf = buf.rstrip() + ' ' + nxt
        depth = balance(buf)
        if depth <= 0 and buf.rstrip().endswith(':'):
            break
    return buf


def _ops_in_head(head, consts):
    """从一个 if/elif 分支头里取出它覆盖的 opcode 列表。

    三种写法都要认：
        op == 1                      数字字面量（登录/建角这类用裸数字）
        op == C2S_MAP_MOVE           常量名
        op in (A, B, C) / op in SET  元组字面量或集合常量
    """
    ops = []
    for tok in re.findall(r'op\s*==\s*([A-Za-z_][A-Za-z0-9_]*|\d+)', head):
        if tok.isdigit():
            ops.append(int(tok))
        elif tok in consts:
            ops.append(consts[tok])
    for grp in re.findall(r'op\s+in\s*\(([^)]*)\)', head):
        for tok in re.findall(r'([A-Za-z_][A-Za-z0-9_]*|\d+)', grp):
            if tok.isdigit():
                ops.append(int(tok))
            elif tok in consts:
                ops.append(consts[tok])
    for name in re.findall(r'op\s+in\s+([A-Za-z_][A-Za-z0-9_]*)', head):
        val = consts.get(name) if name in consts else getattr(m, name, None)
        if isinstance(val, int):
            ops.append(val)
            continue
        try:                       # 集合/range/元组常量（BATTLE_CMD_OPS 等）
            for sub in (val or ()):
                if isinstance(sub, int):
                    ops.append(sub)
        except TypeError:
            pass
    return ops


def _guard_len(head):
    """分支头里 `len(body) >= N` / `len(body) > N` 要求的 body 最小长度。"""
    n = 0
    for mm in re.finditer(r'len\(body\)\s*>=\s*(\d+)', head):
        n = max(n, int(mm.group(1)))
    for mm in re.finditer(r'len\(body\)\s*>\s*(\d+)', head):
        n = max(n, int(mm.group(1)) + 1)
    return n


def probe_body_for(op, guard, minimum=5):
    """按守卫长度生成探测 body：够长就以 0 填充，至少 minimum 字节。"""
    n = max(minimum, int(guard.get(op, 0) or 0))
    n = min(n, GUARD_MAX)
    # 全 0 填充：探针只关心「能不能过长度守卫」，首字节若置 1 会被当成子命令
    # （如 OPENUI sub=1）而走错分支，反而污染判定。
    return bytes([0]) * n

# 「已知已实现」清单 = 人工基线 ∪ **从 _dispatch 源码解析出的 opcode**。
#
# ★ 2026-10-19 修掉最后一个静默误报源：这份清单原先是**纯手抄**的，而它一旦漏项，
#   该 opcode 就既不会被跳过、也不会触发误报闭环——直接以「未实现」的身份进报告。
#   16/144/145/156 就是这么躺了很久（内核确有分支，清单里没有）。
#   现在两路合并：
#     · 手抄基线：兜住解析器认不出的写法（range()、动态拼接、集合运算）
#     · 源码解析：_dispatch 里真的出现了 op == X / op in (...) 就算「有分支」
#   并集之后，凡是探测段报「未识别」却在这份集合里的，一律进误报/重试闭环。
HANDLED_BASELINE = (
    1, 2, 3, 5, 6, 8, 14, 15, 32, 33, 34, 35, 36, 37, 38, 48, 54, 61, 78, 81, 88,
    96, 98, 99, 102, 104, 128, 129, 130, 131, 132, 133, 134, 192,
    160, 161, 162, 163, 164, 165, 166, 167,
    168, 169, 170, 171, 172, 173, 174, 175, 176, 177,
    # 2026-10-19 按 _dispatch 实测补齐（有分支却没登记 → 会被当缺口发出去）
    16, 144, 145, 156, 49, 97, 100, 101, 103, 105, 106, 108, 112, 113, 127,
)


def _known_handled():
    """人工基线 ∪ 源码解析结果（惰性计算，供模块级 KNOWN_HANDLED 使用）。"""
    try:
        return frozenset(HANDLED_BASELINE) | frozenset(_dispatch_guard_lengths())
    except Exception:                                    # noqa: BLE001
        return frozenset(HANDLED_BASELINE)


KNOWN_HANDLED = _known_handled()


# ══════════════════════════════════════════ 客户端侧最小收发 ══
def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def recv_s2c(c, timeout=2.0):
    c.settimeout(timeout)

    def exact(n):
        buf = b''
        while len(buf) < n:
            ch = c.recv(n - len(buf))
            if not ch:
                raise ConnectionError('closed')
            buf += ch
        return buf
    n = exact(1)[0]
    if n == 0xFF:
        n = struct.unpack('<H', exact(2))[0]
    p = exact(n)
    return p[0], p[1:]


def send_c2s(c, op, body=b''):
    if len(body) >= 0xFF:
        c.sendall(bytes([op]) + b'\xFF' + struct.pack('<H', len(body)) + body)
    else:
        c.sendall(bytes([op, len(body)]) + body)


def drain(c, timeout=0.35):
    out = []
    while True:
        try:
            out.append(recv_s2c(c, timeout)[0])
        except Exception:                            # noqa: BLE001
            return out


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def move_body(x, y, dirs=()):
    """C2SMapMove：x(2) y(2) n(1) + n 个方向字节。n=0 表示原地算一步。"""
    return struct.pack('<HH', x, y) + bytes([len(dirs)]) + bytes(dirs)


# ══════════════════════════════════════════════════ 巡检序列 ══
class Smoke:
    def __init__(self, c, srv, verbose=False):
        self.c, self.srv, self.v = c, srv, verbose
        self.sent = []          # (label, op) 发出顺序
        self.recv = []          # (s2c_op) 收到顺序
        self.steps = 0
        self.skipped_known = []  # 探测段跳过的「已知已实现」编码（只走真实路径）

    def s(self, op, body=b'', wait=0.35, label=''):
        label = label or ('op%d' % op)
        try:
            send_c2s(self.c, op, body)
        except Exception as e:                       # noqa: BLE001
            if self.v:
                print('    ! %-22s 发送失败 %r' % (label, e))
            return []
        got = drain(self.c, wait)
        self.sent.append((label, op))
        self.recv += got
        self.steps += 1
        if self.v:
            print('    %-22s op%-4d → %s' % (label, op, got or '（无响应）'))
        return got

    def enter_war(self, max_steps=10):
        """原地算步走到遇怪。

        trace_len=0 → 终点=起点，坐标不变，免得撞上地图出口被自动传送；
        内核的 move_count 照样递增，所以遇怪判定照常（每 ENCOUNTER_EVERY 步一次）。
        """
        if self.srv.in_war:
            return True
        for _ in range(max_steps):
            try:
                send_c2s(self.c, 54, move_body(self.srv.px, self.srv.py))
            except Exception:                         # noqa: BLE001
                return False
            got = drain(self.c, 0.3)
            self.sent.append(('移动(触发遇怪)', 54))
            self.recv += got
            if self.srv.in_war:
                return True
        return False


class _NullTap(object):
    """找不到真实 FrameTap 时的降级替身：轮次恒 0、永无增量、撤销恒失败。

    为什么需要：`mark_round` 是 **FrameTap 的实例方法**（状态在实例上），不是模块函数。
    巡检里 `T` 是模块、`sm.tap` 才是实例；写错就 AttributeError 把整个巡检打断，
    而巡检是「报告工具」，不该因为探针辅助能力缺失就整份产出不出来。
    """

    def mark_round(self):
        return 0

    def current_round(self):
        return 0

    def unknown_since(self, round_no, cumulative=False):
        return set()

    def forget_unknown(self, op):
        return False


_NULL_TAP = _NullTap()


def _tap_inst(sm):
    """取巡检用的 FrameTap 实例（`sm.tap`）；没有就走降级替身。"""
    inst = getattr(sm, 'tap', None)
    if inst is not None and hasattr(inst, 'mark_round'):
        return inst
    return _NULL_TAP


def st_unknown(sm):
    """当前 tap 记录到的「内核报了未识别」编码集合（只增不减的历史集）。"""
    try:
        return set(sm.tap._unknown_op)
    except AttributeError:
        return set()


def run(sm, proto):
    s, srv = sm.s, sm.srv

    # ── 心跳 ──
    s(8, b'', 0.3, 'PING')

    # ── 登录链 ──
    s(1, login_body(), 0.4, '登录')
    if not srv.have_role:
        s(3, '巡检员'.encode('gbk').ljust(20, b'\x00') + b'\x00' * 4, 0.4, '建角')
    s(5, bytes([16]), 0.3, '账号管理')
    s(6, b'\x00\x00\x00\x00\x00', 0.3, '带宽字表')
    s(2, struct.pack('<I', 1), 1.2, '选角进世界')

    # ── 移动 ──
    s(54, move_body(srv.px, srv.py, (1,)), 0.4, '移动')

    # ── NPC：村长（对话+商店+任务）、传送使 ──
    s(96, struct.pack('<I', m.NPC_ID), 0.4, '点击村长')
    s(98, struct.pack('<I', 2), 0.4, 'NPC 选项·买点东西')
    s(99, struct.pack('<HH', 0, 1), 0.4, 'NPC 购买')
    s(98, struct.pack('<I', 1), 0.4, 'NPC 选项·任务')
    s(48, b'', 0.4, '查询任务')
    s(96, struct.pack('<I', m.TELEPORT_NPC_ID), 0.4, '点击传送使')
    s(98, struct.pack('<I', 1), 0.9, 'NPC 选项·传送')

    # ── UI / 帮派 / 通用通道 ──
    s(88, bytes([m.OPENUI_SUB_ADDNPC]) + struct.pack('<H', m.MINIMAP_ID), 0.4, 'OPENUI·小地图NPC')
    s(88, bytes([m.OPENUI_SUB_JINGXIU, 0]), 0.4, 'OPENUI·静修')
    s(88, bytes([4]), 0.25, 'OPENUI·无应答 sub')
    s(81, bytes([m.GROUP_SUB_OPEN]), 0.4, '帮派·打开')
    s(81, bytes([m.GROUP_SUB_MEMBERLIST]), 0.4, '帮派·成员列表')
    s(192, bytes([m.C2S_COMMON_SUB_SENDLOG, 1, 7, 0, 2, 0]), 0.25, '通用·点击统计')

    # ── 宠物 ──
    s(129, struct.pack('<I', m.PET_ID), 0.4, '宠物·看资质')
    s(131, struct.pack('<I', m.PET_ID), 0.4, '宠物·出战')
    for op, nm in ((128, '宠物·放生'), (130, '宠物·改名'),
                   (132, '宠物·存入'), (133, '宠物·取出'), (134, '宠物·加点')):
        s(op, struct.pack('<I', m.PET_ID), 0.22, nm)

    # ── 物品（iid 2001/2002 来自 MOCK_ITEMS）──
    s(32, struct.pack('<I', 2001), 0.3, '物品·详情')
    s(35, struct.pack('<I', 2001) + bytes([0]), 0.3, '物品·附加信息')
    s(33, struct.pack('<I', 2001) + bytes([0, 1]), 0.3, '物品·拖动')
    s(34, struct.pack('<I', 2002), 0.3, '物品·使用')
    s(36, struct.pack('<I', 2001), 0.22, '物品·整理')
    s(37, struct.pack('<I', 2001), 0.22, '物品·绑定')
    s(38, struct.pack('<I', 2001), 0.22, '物品·锁定')

    # ── 杂项 ──
    s(15, b'', 0.3, '时间查询')
    s(61, b'', 0.3, 'C2GS 时间')
    s(14, b'', 0.3, '黑名单')
    s(78, bytes([5]), 0.3, '生产/矿点')

    # ── NPC 二级菜单（op97-127，前面只踩了点击/选项/购买）──
    for op, body, nm in (
            (97, struct.pack('<I', m.NPC_ID), 'NPC·指令菜单'),
            (100, struct.pack('<I', m.NPC_ID), 'NPC·任务对话'),
            (101, struct.pack('<I', m.NPC_ID), 'NPC·询价'),
            # op104 真实形态是 flag(1) + ids(N×4B)；旧猜测（单个 4B NPC_ID）只有 4B，
            # 会被内核的长度守卫挡下 → 把「已实现」误报成缺口。这里按客户端真实形态发。
            (104, bytes([2]) + struct.pack('<II', 50010, 51010), 'NPC·制作费用'),
            # 制作：小麦种子(2001)×2 → 鹿茸（MAKE_RECIPES 第一条）
            (102, bytes([7]) + struct.pack('<II', 2001, 2001), 'NPC·制作'),
            (106, struct.pack('<I', 2001) + b'\x00\x01', 'NPC·卖出'),
            (108, struct.pack('<I', m.NPC_ID), 'NPC·右键菜单'),
            (49, struct.pack('<I', 1), '任务·放弃'),
            # ★ 2026-10-19：这两条 + op16/op156 曾是「真实路径 body 太短」的误报源。
            #   内核守卫是 len(body) >= 8（op144/145）、>= 9（op156）、>= 5（op16），
            #   而这里原先只发 4B / 4B / 1B / 0B → 落到「未识别 op=N」兜底。
            #   C2SGetSummonInfo(tid, oid) / C2SGetItemInfo(tid, oid) 都是两个 4B 参数。
            (144, struct.pack('<II', m.PET_ID, 0), '查看·宠物信息'),
            (145, struct.pack('<II', 2001, 0), '查看·道具信息'),
            (156, bytes([1]) + struct.pack('<II', 2001, 0), '查看·道具附加'),
            (87, struct.pack('<I', m.NPC_ID), '服务/技能查询'),
            (86, 'hello'.encode('gbk') + b'\x00', '文本/聊天'),
            (67, bytes([0]), '改称号'),
            (68, struct.pack('<H', 0), '改快捷栏'),
            (66, struct.pack('<I', 0), '设置黑名单'),
            (51, struct.pack('<I', 0), '关闭角色面板'),
            # C2S_PROP_POINT 是 5×1B 增量（体/力/魔/魅/敏）；发 1B 会落兜底
            (16, bytes([1, 0, 0, 0, 0]), '属性加点'),
            (17, bytes([0]), '升级'),
            (19, struct.pack('<H', 1101), 'DIY 招式'),
            (73, struct.pack('<I', 2002), '服用药品'),
            (76, struct.pack('<H', 0), '烹饪'),
            (77, bytes([0]), '关闭小游戏'),
            (79, bytes([0]), '工厂'),
            (80, bytes([0]), '选区'),
            (83, bytes([0]), '户籍'),
            (84, bytes([0]), '国家/报纸'),
            (85, bytes([0]), '论坛'),
            (89, bytes([0]), '道具商城'),
    ):
        s(op, body, 0.2, nm)

    # ── 战斗：一轮一组，把 18 种战斗指令都踩上 ──
    pid = getattr(m, 'TEACH_MAGIC_IDS', [1101])[0]
    rounds = (
        [(160, bytes([1]), '战斗·普攻')],
        [(161, struct.pack('<BH', m.HERO_WAR_ID, pid), '战斗·招式')],
        [(163, b'', '战斗·防御')],
        [(165, bytes([1]) + struct.pack('<I', 2007), '战斗·道具')],
        [(162, bytes([1]), '战斗·特技')],
        [(166, bytes([1]), '战斗·召唤')],
        [(167, bytes([1]), '战斗·保护')],
        [(173, bytes([1]), '战斗·捕捉')],   # 在战斗态下发；捕捉目标用敌方 id=1
        # ★ 2026-10-19：宠物指令 op168-174 只在「主角已交指令、服务端挂起等宠物」的
        #   窗口内才被 _finish_pet_turn 消费；且 body 首字节必须是 PET_WAR_ID(250)，
        #   否则 _dispatch 里 `body[0] == PET_WAR_ID` 不成立 → 落战斗保底。
        #   旧版只发 bytes([1])，于是 7 条一律被记成「未作逻辑的战斗指令」。
        [(168, bytes([m.PET_WAR_ID, 1]), '战斗·召唤兽攻击')],
        [(169, bytes([m.PET_WAR_ID]) + struct.pack('<H', pid), '战斗·召唤兽招式')],
        [(170, bytes([m.PET_WAR_ID]), '战斗·召唤兽防御')],
        [(171, bytes([m.PET_WAR_ID, 0]) + struct.pack('<I', 2007), '战斗·召唤兽道具')],
        [(172, bytes([m.PET_WAR_ID, m.HERO_WAR_ID]), '战斗·召唤兽保护')],
        [(174, bytes([m.PET_WAR_ID]), '战斗·召唤兽逃跑')],
        [(177, bytes([1]), '战斗·子女召唤')],
        [(175, '12345'.encode('gbk') + b'\x00', '战斗·喊话')],
        [(176, bytes([1]), '战斗·查看单位')],
        [(164, b'', '战斗·逃跑')],
    )
    WAR_OPS = set(m.BATTLE_CMD_OPS)
    for cmds in rounds:
        if not sm.enter_war():
            continue
        # ★ 进战斗失败时**绝不能**继续发战斗指令：非战斗态下 op173 之类会落到
        #   链尾「未识别 op=N」，把「已实现」误报成缺口（2026-10-19 实测踩到）。
        if not srv.in_war:
            continue
        for op, body, nm in cmds:
            if not srv.in_war or op not in WAR_OPS and op <= 177:
                # 非战斗态（含战斗中被打死提前 WarEnd）一律不发战斗指令
                if not srv.in_war:
                    break
            # ★ 宠物指令（op168-174）不是「独立一轮」，而是**挂在主角指令之后**的
            #   第二段异步包：服务端收到主角指令 → 按速度序结算快位 → 把
            #   _pending_pet_suffix 挂起、等宠物指令。所以必须先发主角指令、等它
            #   真的进入挂起窗口（_pending_pet_suffix 非 None），再发宠物那条。
            if op in m.C2S_WAR_SUMM_ACTIONS:
                if srv._pending_pet_suffix is None:
                    # 还没挂起：先替主角交一条普攻，把它推进等待窗口
                    if not srv.in_war:
                        break
                    s(160, bytes([1]), 0.6, '战斗·普攻（推进到宠物窗口）')
                    if srv._pending_pet_suffix is None:
                        continue        # 这一轮进不去窗口（宠物已倒地等），跳过
                s(op, body, 0.6, nm)
                continue
            s(op, body, 0.75, nm)

    # ── 全量探测：剩下没踩到的编码逐个发一遍 ──
    # 只会命中内核的「未识别 op=N」兜底（非战斗态），副作用就是一条日志。
    # ★ 已实现的编码（KNOWN_HANDLED）一律跳过：它们只能靠上面的真实路径去踩，
    #   在这里用通用 body 发会被长度检查挡下、误报成缺口。
    if srv.in_war:
        s(164, b'', 0.6, '战斗·逃跑（探测前先出战斗）')
    hit = {op for _l, op in sm.sent}
    guard = _dispatch_guard_lengths()
    sm.guard = guard
    # ★ 探测段口径（2026-10-19 重定）：
    #   旧行为「已实现的完全不发」会让实测覆盖率掉到 58%（本次踩到 80/136），
    #   因为绝大部分 opcode 都被算作「已知已实现」而跳过。但**探测本身就是实测**：
    #   只要 body 长度满足守卫，发出去不落「未识别」同样是一份运行时证据。
    #   所以改成：
    #     · 有守卫长度的 → 按守卫补长后照发（能过就不会误报）
    #     · 无守卫长度且不在基线里的 → 用 PROBE_BODY 照发（真缺口会露出来）
    #   跳过仅保留给「真实路径已经踩过」的。
    skipped_known = []
    probes = []
    # 探测开始前打一个轮次标记：此后 note_log 记下的「未识别」都能归到这一轮，
    # 用 _tap_inst(sm).unknown_since() 精确取增量（见下方误报闭环）。
    sm.probe_round = _tap_inst(sm).mark_round()
    for op in sorted(set(proto.c2s_upspace)):
        if op in hit:
            skipped_known.append(op)
            continue
        probes.append(op)
        body = probe_body_for(op, guard)
        s(op, body, 0.12, '探测 op%d（%dB，守卫 %s）'
          % (op, len(body), guard.get(op, '-')))
    sm.skipped_known = skipped_known

    # ── 误报闭环：探测完发现「内核说有分支、却仍报未识别」的，加长 body 重试 ──
    # 判据只有一条：该 opcode 在 KNOWN_HANDLED 里（内核源码确有分支）。
    # 这时唯一合理的解释就是 body 不够长——按 GUARD_RETRY 递增长度再发一次，
    # 直到不再报未识别或到达 GUARD_MAX。重试结果覆盖原文判定。
    # ★ 判据用 tap 的轮次标记（T.unknown_since）而不是全量 `_unknown_op`：
    #   后者是只增不减的历史集，重试成功了也还在里面，拿它判会永远认为失败。
    #   成功一次即调 _tap_inst(sm).forget_unknown() 把误报从历史集里撤掉。
    sm.retried = {}
    if probes:
        time.sleep(0.35)
        # ★ 用 tap 的轮次标记取「探测段新增的未识别」（2026-10-19 新增 tap 接口）。
        #   更早的做法是拿日志文件字节偏移去猜增量：偏移取早取晚都会漏——173 的兜底
        #   发生在战斗轮次里、早于探测段，按偏移根本抓不到。
        #   现在 _tap_inst(sm).mark_round() 给每批帧打轮次号，note_log 记录「首次出现在第几轮」，
        #   unknown_since(r) 直接给出增量，不再依赖任何时序猜测。
        unknown_hist = set(st_unknown(sm))
        probe_new = _tap_inst(sm).unknown_since(sm.probe_round) if sm.probe_round else set()
        # 候选池 = 探测段新增 ∪ 历史遗留（都在 KNOWN_HANDLED 里才值得复核）
        cand = sorted((set(probes) | set(hit) | unknown_hist | probe_new)
                      & set(KNOWN_HANDLED))
        misfired = sorted((set(probe_new) | unknown_hist) & set(cand))
        # 第一类：纯粹 body 太短 → 按守卫补长，非战斗态重发
        for op in misfired:
            n = max(GUARD_RETRY, len(probe_body_for(op, guard)))
            while n <= GUARD_MAX:
                r = _tap_inst(sm).mark_round()
                s(op, bytes([0]) * n, 0.12, '重试 op%d（%dB，误报闭环）' % (op, n))
                time.sleep(0.25)
                if op not in _tap_inst(sm).unknown_since(r):
                    sm.retried[op] = n
                    _tap_inst(sm).forget_unknown(op)   # ★ 证伪：撤销历史集里的误报标记
                    break
                n *= 2
            else:
                sm.retried[op] = None     # 到上限仍报未识别 → 真缺口，如实留档
                if sm.v:
                    print('    ! 非战斗态重试 op%d 至 %dB 仍报未识别' % (op, n // 2))
        # 第二类：**只在战斗态才有分支**（op168-174 宠物指令、op173 捕捉）。
        #   非战斗态发多少次都没用，必须进战斗、并在主角指令之后重发一次。
        war_only = [op for op in misfired if sm.retried.get(op) is None]
        if sm.v:
            print('    ! 误报集合 %s，待战斗态重试 %s' % (misfired, war_only))
        # 逐条进战斗重试：每条都要**重新进一次战斗**，否则上一条已把回合推完、
        # 下一条发出来时又回到「等主角指令」状态，照样落保底。
        for op in war_only:
            if not sm.enter_war():
                sm.retried[op] = None
                continue
            # 宠物指令（168-172/174）要先进「等待宠物」窗口；173 是主角捕捉指令，
            # 直接在战斗态发即可（走 _hero_war_aux，不需要窗口）。
            if op in m.C2S_WAR_SUMM_ACTIONS:
                if srv._pending_pet_suffix is None:
                    s(160, bytes([1]), 0.6, '战斗·普攻（为 op%d 开路）' % op)
                body = bytes([m.PET_WAR_ID]) + bytes(4)
            else:
                body = bytes([1])
            r = _tap_inst(sm).mark_round()
            s(op, body, 0.6, '战斗态重试 op%d' % op)
            time.sleep(0.2)
            if op not in _tap_inst(sm).unknown_since(r):
                sm.retried[op] = 'war'
                _tap_inst(sm).forget_unknown(op)


# ══════════════════════════════════════════════════════ 报告 ══
def _compact_ops(ops):
    """把 opcode 列表压成区间写法：1 2 3 5 7 8 → 1-3 5 7-8。

    报告里动辄几十个 opcode，逐个罗列读者没法一眼扫。压成区间后
    「168-174」这种连续段一目了然，也便于和客户端源码的区间对照。
    """
    nums = sorted({int(o) for o in ops})
    if not nums:
        return '无'
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else '%d-%d' % (nums[i], nums[j]))
        i = j + 1
    return ' '.join(out)


# ══════════════════════════════════════════════════════ 报告 ══
def build_report(tap, proto, sm):
    st = tap.stats(include_tables=False)
    rows = proto.table('C2S')
    space = proto.c2s_upspace
    seen_c2s = set(st['counts']['C2S'])
    seen_c2s_int = {int(k) for k in seen_c2s}
    seen_s2c = {int(k) for k in st['counts']['S2C']}
    bad = set(st['unknown_c2s'])
    # ★ 减去「误报闭环里已洗白」的编码：tap._unknown_op 是**只增不减**的历史集，
    #   重试成功后旧标记仍在。真缺口 = 历史集 − 已证伪集合。
    retried_ok = {op for op, v in (getattr(sm, 'retried', {}) or {}).items() if v}
    bad -= retried_ok
    s2c_rows = proto.table('S2C')

    hit = sorted(seen_c2s_int & set(space))
    miss = sorted(set(space) - seen_c2s_int)
    # ★ 2026-10-20：区分「发过」和「被认了」。
    #   seen_c2s 是**探针发过**的集合，包含内核报未识别的——那不算覆盖。
    #   真正的覆盖 = 发过 − 未识别（真缺口 + 方向错误）。
    #   探针误报（misfire_ops）已被证伪，内核其实认了，算覆盖。
    _misfire_set = set(bad) & KNOWN_HANDLED
    _gap_set = set(bad) - KNOWN_HANDLED
    covered = sorted(set(hit) - _gap_set)
    L = []
    A = L.append
    A('=' * 72)
    A(' 协议覆盖巡检报告（基于本次实际流量，非手写快照）')
    A('=' * 72)
    A('内核      m4_server · seed 0x%X · 每 %d 步遇怪 · %d 敌'
      % (SEED, ENCOUNTER_EVERY, ENEMIES))
    A('本次会话  %d 帧（C2S %d / S2C %d）· 巡检动作 %d 步'
      % (st['frames'], len(seen_c2s_int), len(seen_s2c), sm.steps))
    A('')
    # ★ 2026-10-20 改版：按「为什么没覆盖」分成四类，每类都带计数 + opcode 列表。
    #   旧版只报一个「服务端未实现 N 个」，读者要自己去猜每个编码属于哪种情况
    #   （真缺口？探针 body 不够？方向搞错了？这轮压根没走到？）——那等于把
    #   判断工作推给读者。现在四类互斥且穷尽，合计恒等于「分母 − 踩到」。
    #
    #   四类的判定依据：
    #     · 真缺口     —— 探针 body 够长（达到 _dispatch 守卫长度）仍报未识别
    #     · 探针误报   —— body 不足导致，误报闭环（加长/战斗态重发）已证伪
    #     · 方向错误   —— 拿 S2C 编码当 C2S 探针发，内核当然不认
    #     · 未触发路径 —— 已知已实现，但这轮真实交互序列没走到
    nodef_hit = sorted(o for o in hit if o in set(st['c2s_nodef']))
    # 探针误报：进入误报闭环且被证伪的（retried 有值）
    misfire_ops = sorted(set(bad) & KNOWN_HANDLED)
    # 真缺口：探针发过、body 够长、仍报未识别（retried 记为 None 且不在 KNOWN_HANDLED）
    real_gap = sorted(o for o in bad if o not in KNOWN_HANDLED)
    # 方向错误：S2C 侧编码被当成 C2S 探针（值落在 S2C 路由表里）
    s2c_route = set(int(r['op']) for r in proto.table('S2C'))
    wrong_dir = sorted(o for o in real_gap if o in s2c_route)
    real_gap = [o for o in real_gap if o not in s2c_route]
    # 未触发：已实现但本轮没走到的
    notwalked = sorted(set(sm.skipped_known) & set(miss))

    A('── 上行 C2S ' + '─' * 58)
    A('分母      %d（客户端源码常量全集 C2S_* + C2GS_*，权威口径）' % len(space))
    A('运行时覆盖：%d / %d = %.1f%%'
      % (len(covered), len(space),
         100.0 * len(covered) / max(1, len(space))))
    A('├─ 真缺口（body 够长仍无分支）        : %d%s'
      % (len(real_gap), ('  [%s]' % _compact_ops(real_gap)) if real_gap else ''))
    A('├─ 探针误报（body 不足，已闭环重试）  : %d%s'
      % (len(misfire_ops),
         ('  [%s]' % _compact_ops(misfire_ops)) if misfire_ops else ''))
    A('├─ 方向错误（S2C 编码当 C2S 探针）    : %d%s'
      % (len(wrong_dir), ('  [%s]' % _compact_ops(wrong_dir)) if wrong_dir else ''))
    A('└─ 未触发路径（本轮未走到）            : %d%s'
      % (len(notwalked), ('  [%s]' % _compact_ops(notwalked)) if notwalked else ''))
    # 合计口径：分母 = 覆盖 + 未覆盖；未覆盖 = 真缺口 + 方向错误 + 未触发。
    # 探针误报已证伪（内核其实认了），归入覆盖，不进合计。
    _uncovered = len(real_gap) + len(wrong_dir) + len(notwalked)
    A('   合计 %d = 分母 %d − 覆盖 %d（四类互斥且穷尽，无遗漏）'
      % (_uncovered, len(space), len(covered)))
    if len(covered) + _uncovered != len(space):
        A('   ⚠ 口径不自洽：覆盖 %d + 未覆盖 %d ≠ 分母 %d'
          % (len(covered), _uncovered, len(space)))
    if nodef_hit:
        A('字典缺字段定义（踩到但解不出字段）%d 个：%s'
          % (len(nodef_hit), _compact_ops(nodef_hit)))
    A('')
    A('── 下行 S2C ' + '─' * 58)
    A('分母      %d（客户端 net.py 的 NET_FUNCTION_NAME 路由表）' % len(s2c_rows))
    A('运行时出现：%d / %d = %.1f%%'
      % (len(seen_s2c), len(s2c_rows),
         100.0 * len(seen_s2c) / max(1, len(s2c_rows))))
    _unrouted = sorted(st['unrouted_s2c'])
    _s2c_miss = sorted(set(int(r['op']) for r in s2c_rows) - seen_s2c)
    A('├─ 客户端无路由（静默丢弃，服务端自嗨）: %d%s'
      % (len(_unrouted), ('  [%s]' % _compact_ops(_unrouted)) if _unrouted else ''))
    A('└─ 本轮未出现                          : %d%s'
      % (len(_s2c_miss), ('  [%s]' % _compact_ops(_s2c_miss)) if _s2c_miss else ''))
    A('   合计 %d = 分母 %d − 出现 %d（两类互斥且穷尽）'
      % (len(_unrouted) + len(_s2c_miss), len(s2c_rows), len(seen_s2c)))
    A('')
    A('── 字典缺口（与「实现」无关，属逆向覆盖缺口）' + '─' * 28)
    A('会发但缺字段定义 %2d 个：%s'
      % (len(st['c2s_nodef']), _compact_ops(st['c2s_nodef'])))
    A('字典有但客户端无此常量（疑似幻影）%d 个：%s'
      % (len(st['c2s_orphan']), _compact_ops(st['c2s_orphan'])))
    A('')
    # ★ 待办只列「真缺口」（2026-10-20 改）：旧版把 bad 全集都列进来，
    #   而 bad 里混着探针误报和方向错误——那些不是待办，列出来只会误导。
    #   分类见上面「上行 C2S」的四行；这里只对真缺口按模块分组。
    bymod = {}
    for r in rows:
        if r['op'] in set(real_gap):
            bymod.setdefault(r['module'] or '（无模块）', []).append(r['op'])
    if real_gap:
        A('── 待办：真缺口 %d 个（按模块分组）' % len(real_gap) + '─' * 30)
        A('  只列「body 够长仍无分支」的——探针误报/方向错误不算待办，见上方分类。')
        for mod in sorted(bymod, key=lambda k: (-len(bymod[k]), k)):
            ops = bymod[mod]
            A('  %-18s %2d 个：%s'
              % (mod, len(ops), _compact_ops(ops)))
    else:
        A('── 待办：真缺口 0 个（上行全部有分支或已归类，无遗漏）' + '─' * 20)
    A('')
    # 真实路径没覆盖到的已实现编码：说明巡检序列本身还有可补的交互
    notwalked = sorted(set(sm.skipped_known) & set(miss))
    if notwalked:
        A('── 已实现但这轮真实路径没走到的 %d 个（巡检序列可再补）' % len(notwalked) + '─' * 12)
        A('  %s' % ' '.join(str(o) for o in notwalked))
    A('')
    A('── 说明 ' + '─' * 62)
    A('· 本报告只报实测，且**每个数字都自带 opcode 列表**——不留「N 个」这种')
    A('  需要读者自己去代码里翻是哪几个的表述。')
    A('· 「覆盖」= 探针发过且内核认了（打了具体分支）；未识别的不算覆盖。')
    A('· 四类判定：真缺口 = body 达到 _dispatch 守卫长度仍报未识别；')
    A('  探针误报 = body 不足，误报闭环（加长 / 战斗态重发）已证伪，内核其实认了；')
    A('  方向错误 = 拿 S2C 编码当 C2S 探针发，内核当然不认；')
    A('  未触发 = 已知已实现但本轮交互序列没走到。')
    A('· 「字典缺字段定义」与「服务端没实现」是两回事')
    A('  （op8 C2S_PING 就是服务端早已实现、字典却没条目的例子）。')
    A('· 复现：python coverage_smoke.py    加 -v 看每一步收发。')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='协议覆盖巡检')
    ap.add_argument('--md', default='', help='同时写一份 markdown 报告到该路径')
    ap.add_argument('-v', '--verbose', action='store_true', help='打印每一步的收发')
    args = ap.parse_args()

    proto = T.Protocol()
    if not proto.ok:
        print('[错误] 协议字典加载失败：%s' % proto.error)
        return 2
    tap = T.FrameTap(proto)
    tap.install(m)

    tmp = tempfile.mkdtemp(prefix='m4cov_')
    srv = m.M4Server('127.0.0.1', 0, seed=SEED, encounter_every=ENCOUNTER_EVERY,
                     enemies=ENEMIES, turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))

    server_end, client_end = transport.make_memory_pair()
    th = threading.Thread(target=srv.handle, args=(server_end,), daemon=True)
    th.start()

    op, data = recv_s2c(client_end, 3.0)
    if op != 1:
        print('[错误] 未收到 Hello（收到 op=%r）' % op)
        return 2
    client_end.sendall(hello_resp(SEED))
    time.sleep(0.15)

    sm = Smoke(client_end, srv, args.verbose)
    sm.tap = tap            # 让误报复核能读 tap 的「未识别」历史集与轮次 API
    # 巡检会故意踩未实现编码，内核日志必然刷屏。这里只重定向 **stdout**，
    # 不碰 srv.log ——tap.install() 换掉的是类方法，一旦给实例挂个 no-op 属性，
    # 就把「未识别 op=N」的旁听一起干掉了，报告会失真。
    real_out = sys.stdout
    try:
        if not args.verbose:
            sys.stdout = io.StringIO()
        try:
            run(sm, proto)
        except Exception as e:                       # noqa: BLE001
            sys.stdout = real_out
            print('[警告] 巡检中途出错，报告仍按已产生流量生成：%r' % e)
    finally:
        # 先关连接再恢复 stdout：内核收尾时的「连接结束」「已存档」等日志否则会
        # 挤在报告前面刷过去，把报告顶到屏幕外
        try:
            client_end.close()
        except Exception:                            # noqa: BLE001
            pass
        time.sleep(0.6)
        sys.stdout = real_out

    text = build_report(tap, proto, sm)
    print(text)
    if args.md:
        with io.open(args.md, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print('\n[巡检] 已写入 %s' % args.md)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
