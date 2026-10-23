# -*- coding: utf-8 -*-
"""战斗编排器：单步重放 + 并排伤害对照。

它解决什么问题
--------------
伤害公式的验证一直停在**纯函数层**：`damage_model.physical_damage` 拿到 (A,D,P,Lt)
之后算得对不对，由 `tests/_test_damage.py` 守着。但那之前还有一整段没人验：

    C2S 指令 → 内核选目标 → 速度序 → `_dmg()` 的**实参** → 结算 → 表现帧 → hp_chg

这段里任何一处出错，「公式对」都没用。而且出的都是**静默错**：
  · 传错参（拿 HERO_DP 当 P、拿 ENEMY_LEVEL 当 Lt）——数字看着合理，没人会发现；
  · 减了不该减的（防御姿态没减半、保护者没承伤）；
  · hp_chg 符号反了——客户端把伤害当回血；
  · 发帧时机错了（先发 WState 再发 AttackNormal）。

本模块把这条链变成**可单步、可对照、可复现**的：

    ① 单步：把一个真实连接上的完整战斗切成原子步（一帧 = 一步），
       每步给出「谁按了什么 → 预期发什么 → 实际发了什么」。
    ② 并排伤害对照：对同一次攻击，同时给出 reverse / linear 两个模型的伤害、
       以及内核**真实发出去**的 hp_chg。三者并排，差多少一眼看见。
    ③ 跟前端解耦：只依赖 m4_server / damage_model，不 import tap（tap 反过来用我们）。

两个模型的证据强度**不对等**（见 damage_model 模块头）：
  reverse —— 有反编译出处，是「原服公式长什么样」的唯一实证线索；
  linear  —— 无出处（外部受控变法实验表，量级超本作 TBYTE 上限），纯便利。
所以对照表里 reverse 的列标为「参照」，linear 的列标为「对照」——
**它们不是并列候选**，别把并排看成「两个都可能是对的」。

只有标准库。
"""
from __future__ import print_function

import struct


# ════════════════════════════════════ ① 单步：原子步定义 ══════════════════════════
#
# 一步 = 一次「可观测的因果」：一个输入指令/事件 → 一组预期表现帧 → 实际发出的帧。
# 为什么按这个粒度切：
#   · 按 frame 切太碎——一次普攻是 4 帧（117 → 123 → 119 → 125），逐帧比没有意义；
#   · 按 round 切太粗——一回合里主角/宠物/三只敌方都可能动手，错了定位不到；
#   · 按「一次行动」切正好对上 `_resolve_hero_round` 的循环体，也对着人类的直觉。
STEP_KINDS = (
    'hero_attack',      # 主角普攻
    'hero_perform',     # 主角招式
    'hero_defend',      # 主角防御
    'pet_attack',       # 宠物普攻
    'pet_perform',      # 宠物招式
    'enemy_act',        # 单个敌方行动
    'war_start',        # 开战
    'war_end',          # 战斗结束（任一方向）
)

# 每种步「必须出现」的表现帧 op（按顺序的子序列判定，不是全等）：
#   WarTurn(114) 不列入：回合推进帧可能由 `_resolve_hero_round` 在不同时机发，
#   把它列成必须会让「一次我方攻击」与「一整个回合」混淆。
EXPECTED_OPS = {
    'hero_attack':  (117, 123, 119),
    'hero_perform': (122, 123, 119),
    'hero_defend':  (),                 # 防御只有姿态，无演出帧
    'pet_attack':   (117, 123, 119),
    'pet_perform':  (122, 123, 119),
    'enemy_act':    (117, 123, 119),
    'war_start':    (112, 115, 129, 114),
    'war_end':      (113,),
}

# 敌方可能放招式（概率由 ENEMY_SKILL_CHANCE 控制）：此时 117 换成 122。
# 判定时两种都接受，但要在 diff 里说明是哪一种。
ATTACK_OPS = {117, 122}

def attack_chains(frames):
    """把一段帧切成若干「攻击链」，返回 [{att, vic, chg, ops, i}]。

    一条链的形态（客户端源码定的演出顺序）：
        117(att,vic) | 122(id,pid,vic…)  →  123(wid,bf,hp_chg)  →  119(收招)
    Protect(121) 可能插在 117 与 123 之间，其余帧（125 血条 / 114 回合）都忽略。

    ★ 为什么要显式切链：
      一步（一个 C2S 指令的响应窗口）里会有多条链——我方、宠物、反击的敌方。
      不切链就只能「取第一个攻击帧之后的 WState」，实测会把宠物的伤害记成
      主角的（差 2 点，看不出来）。发起者 = att_id，这是唯一可靠的归属依据。
    """
    chains = []
    cur = None
    for i, (op, body) in enumerate(frames):
        if op in ATTACK_OPS:
            if cur is not None:
                chains.append(cur)
            att = body[0] if body else None
            vic = None
            if op == 117 and len(body) >= 2:
                vic = body[1]
            elif op == 122:
                # S2CWarPerform：id(1) pid(2) vic_ids(T 剩余全部)
                rest = body[3:]
                vic = rest[0] if rest else None
            cur = {'att': att, 'vic': vic, 'chg': None, 'ops': [op],
                   'i': i, 'ended': False}
            continue
        if cur is None:
            continue
        if op == 119:
            cur['ended'] = True
            cur['ops'].append(op)
            chains.append(cur)
            cur = None
            continue
        if op == 123 and cur['chg'] is None:
            try:
                _w, _h, life, _wd, chg = read_wstate(body)
                if not (life == 2 and chg == 0):
                    cur['chg'] = chg      # 倒地那发 0 伤害不算
                else:
                    cur['dead_note'] = True
            except ValueError:
                pass                       # 帧体残缺：留给判定去红
        cur['ops'].append(op)
    if cur is not None:
        chains.append(cur)
    return chains


def _bound_step(frames):
    """一步的有效帧范围：我方第一条攻击链 + 紧随的敌方响应。

    为什么要截断：一个指令的响应窗口（0.9s）里可能跨了好几轮，
    不截断就会把下一轮的伤害并进本步，并排表多出莫名其妙的行。
    截到「下一条 117/122 出现之前」为止。
    """
    first_at = next((i for i, (op, _b) in enumerate(frames)
                     if op in ATTACK_OPS), None)
    if first_at is None:
        return len(frames), None
    end_mine = None
    for i in range(first_at + 1, len(frames)):
        op = frames[i][0]
        if op == 119:
            end_mine = i
            break
        if op in ATTACK_OPS:
            break
    if end_mine is None:
        return first_at + 1, first_at
    bound = end_mine + 1
    for i in range(bound, len(frames)):
        if frames[i][0] in ATTACK_OPS:
            bound = i
            break
    return bound, first_at

class Step(object):
    """一个原子步：输入 + 预期 + 实际 + 判定。"""

    __slots__ = ('kind', 'label', 'c2s_op', 'c2s_body', 'frames', 'note',
                 'expect_ops', 'seen_ops', 'ok', 'problems')

    def __init__(self, kind, label, c2s_op=None, c2s_body=b'', expect_ops=(),
                 note=''):
        self.kind = kind
        self.label = label              # 人话，例如「主角 普攻 → 敌方2」
        self.c2s_op = c2s_op
        self.c2s_body = c2s_body
        self.expect_ops = tuple(expect_ops)
        self.frames = []               # [(op, body)]，本步实际收到的 S2C
        self.seen_ops = []
        self.note = note
        self.ok = False
        self.problems = []

    def as_dict(self):
        return {'kind': self.kind, 'label': self.label,
                'c2s_op': self.c2s_op, 'note': self.note,
                'expect_ops': list(self.expect_ops),
                'seen_ops': list(self.seen_ops),
                'frames': len(self.frames), 'ok': self.ok,
                'problems': list(self.problems)}


def _subseq(expect, seen):
    """expect 是否为 seen 的**子序列**（保持相对顺序）。

    用子序列而不是全等：两个行动之间可能夹 WarTurn(114)、HPPercent(125)、
    Protect(121) 这些合法的伴随帧。要求全等会把它们全判成错。
    """
    it = iter(seen)
    return all(any(x == y for y in it) for x in expect)




class StepRunner(object):
    """在一个**真实内核连接**上把战斗切成原子步，逐步驱动 + 逐批评判。

    为什么走真实 socket 而不是进程内直调 `_dispatch`：
      · 帧必须经历真实 encode → wire → decode，字节层错了（帧头/变长串/位序）
        才会暴露。进程内直调拿到的是 Python 对象，字节层的静默错全看不到；
      · `m4_server._dispatch` 是私有契约，编排器不该依赖它；公开面就是协议。
      · 代价是要处理 recv 超时——用「步进窗口」而不是固定 sleep（见 collect）。
    """

    def __init__(self, host, port, timeout=6.0):
        import socket
        import time
        self.host, self.port = host, port
        self.timeout = timeout
        self._socket_mod = socket
        self._time_mod = time
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b''
        self.steps = []
        self.seed = 0
        self.in_war = False

    # ── 最小收发 ──
    def close(self):
        try:
            self.sock.close()
        except Exception:                                   # noqa: BLE001
            pass

    def _recv_exact(self, n):
        """读满 n 字节；超时/断开抛 ConnectionError（上层据此判定「够了」）。"""
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(4096)
            except self._socket_mod.timeout:
                raise ConnectionError('timeout')
            if not chunk:
                raise ConnectionError('closed')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_frame(self):
        """读一帧 S2C，返回 (op, body)。S2C 帧头：[1B N][op][data]，N==0xFF → 0xFF+u16。"""
        n = self._recv_exact(1)[0]
        if n == 0xFF:
            n = struct.unpack('<H', self._recv_exact(2))[0]
        body = self._recv_exact(n)
        return body[0], body[1:]

    def send_c2s(self, op, body=b''):
        """C2S 帧头与 S2C **相反**：[op][1B N][body]。"""
        if len(body) < 0xFF:
            head = struct.pack('<BB', op, len(body))
        else:
            head = struct.pack('<BH', op, 0xFFFF)
        self.sock.sendall(head + bytes(body))

    # ── 登录/进世界（编排测试都要走一遍）──
    @staticmethod
    def login_body(name='100860001', pwd='abcdef'):
        b = bytearray()
        b += bytes([0, 10]) + struct.pack('<H', 43)
        b += name.encode('gbk')[:32].ljust(32, b'\x00')
        b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
        b += bytes([1, 2]) + struct.pack('<H', 73)
        return bytes(b)

    def enter_world(self, hero_id=1):
        """登录 + 选角进世界。返回是否看到 SceneM(96)。"""
        self.send_c2s(1, self.login_body())
        ops = [op for op, _b in self.collect(0.5)]
        self.send_c2s(2, struct.pack('<I', hero_id))
        ops += [op for op, _b in self.collect(0.6)]
        return 96 in ops

    def hello(self, seed=None):
        """完成 25B 裸区握手。seed=None 时先收 Hello 并解出服务端 seed。"""
        op, body = self.recv_frame()
        if op != 1:
            raise AssertionError('首帧应为 Hello(1)，实得 op=%d' % op)
        self.seed = struct.unpack('<I', body[:4])[0] if seed is None else seed
        s = self.seed
        obf = ((s + 0x100000000) % 3227993 + 2562088) ^ 6636321
        self.sock.sendall(bytes([obf & 0xFF, (obf >> 8) & 0xFF,
                                  (obf >> 16) & 0xFF]) + b'\x00' * 22)
        self.collect(0.4)          # 吞掉握手后的杂帧（时间通道等）
        return self.seed

    def _track_war(self, frames):
        """从帧里维护 in_war：WarStart(112) 置真、WarEnd(113) 置假。

        StepRunner 只认 opcode、不碰业务字段；in_war 是帧序层的布尔，
        放这里正合适（单位与伤害归 BattleOracle 解析）。
        """
        for op, _b in frames:
            if op == 112:
                self.in_war = True
            elif op == 113:
                self.in_war = False
        return self.in_war

    def collect(self, window=0.35):
        """在 window 秒内尽量收帧，返回 [(op, body), ...]。"""
        out = []
        deadline = self._time_mod.time() + window
        while True:
            left = deadline - self._time_mod.time()
            if left <= 0:
                break
            self.sock.settimeout(left)
            try:
                out.append(self.recv_frame())
            except ConnectionError:
                break
            except Exception:                                   # noqa: BLE001
                break
        self.sock.settimeout(self.timeout)
        self._track_war(out)
        return out

    # ── 单步驱动 ──
    def step(self, kind, label, c2s_op=None, c2s_body=b'', note='', window=0.6):
        """执行一步：发指令 → 收帧 → 与 EXPECTED_OPS 对照 → 记录并返回 Step。

        判定只看「必须出现的 op 是否按序出现」，以及若干硬不变量（见 _verify）。
        不在这里判伤害数值——伤害是编排器的另一条腿（BattleOracle）。
        """
        st = Step(kind, label, c2s_op, c2s_body, EXPECTED_OPS.get(kind, ()), note)
        if c2s_op is not None:
            self.send_c2s(c2s_op, c2s_body)
        st.frames = self.collect(window)
        st.seen_ops = [op for op, _b in st.frames]
        self._verify(st)
        if kind == 'war_start':
            self.in_war = True
        elif kind == 'war_end':
            self.in_war = False
        # ★ 不在这里因为「见到 113」就置 in_war=False：
        #   113 会跟在我方攻击步的尾巴上（那一轮把怪打死），
        #   把它当成「本步是 war_end」会让下一步的窗口判断失灵。
        #   战斗是否结束由调用方显式问（srv.in_war / r.in_war）。
        self.steps.append(st)
        return st

    def end_step(self, label='战斗结束', window=0.9):
        """显式记一步 war_end。发什么由调用方定（gm_war_win / 逃跑 / 打死最后一只）。"""
        return self.step('war_end', label, None, b'', window=window)

    def _verify(self, st):
        """硬不变量（与具体数值无关、任何一步都该成立的）。"""
        p = st.problems
        if st.expect_ops and not _subseq(st.expect_ops, st.seen_ops):
            p.append('缺少预期帧：期望子序列 %r，实得 %r'
                     % (list(st.expect_ops), st.seen_ops))
        # ★ 演出链收尾：只看**第一条**攻击链，不是整步。
        #   一「步」不是一个回合：我方攻击发完，慢位敌方还会反击，
        #   末帧因此完全可能是敌方的 119、WarTurn(114)、甚至下一轮的第一帧。
        #   第一版要求整步以 119/114 结尾 —— 一遇到反击就误判「没收尾」，
        #   而这恰恰是**正确**的帧序（客户端要的就是这个顺序）。
        #   契约真正要求的是：117/122 之后必须紧跟 123 再接 119，一步都不能缺。
        if st.kind in ('hero_attack', 'pet_attack', 'hero_perform',
                       'pet_perform', 'enemy_act'):
            ops = st.seen_ops
            first = next((i for i, o in enumerate(ops) if o in ATTACK_OPS), None)
            if first is None:
                p.append('没有攻击帧（期望 117 或 122，实得 %r）' % (ops,))
            else:
                # 攻击帧之后必须先有一发 WState(123)，再有一个收招(119)
                rest = ops[first + 1:]
                i_ws = rest.index(123) if 123 in rest else -1
                i_end = rest.index(119) if 119 in rest else -1
                if i_ws < 0:
                    p.append('攻击帧后没有 WState(123)：伤害没下发，'
                             '客户端只播动作不飘字（实得 %r）' % (ops,))
                elif i_end < 0 or i_end < i_ws:
                    p.append('攻击链没收尾(119)：客户端不回位且命令队列不推进，'
                             '表现为卡请等待（实得 %r）' % (ops,))
        # hp_chg 符号不变量：本服约定正数=扣血。出现负数就得当回血处理，
        # 而玩家不可能被攻击后回血——这一条能在字节层抓到「符号约定反了」。
        for op, body in st.frames:
            if op == 123 and len(body) >= 4:
                chg = struct.unpack('<i', body[1:5])[0]
                if chg < 0:
                    p.append('WState hp_chg=%d 为负：本服约定正数=扣血，'
                             '负值会被客户端当回血' % chg)
        st.ok = not p
        return st.ok

    # ── 汇总 ──
    def report(self):
        """人话汇总：每步一行，最后给通过数。"""
        lines = []
        for i, st in enumerate(self.steps, 1):
            flag = 'OK  ' if st.ok else 'FAIL'
            lines.append('%s #%d %-10s %s' % (flag, i, st.kind, st.label))
            if not st.ok:
                for pr in st.problems:
                    lines.append('       \u2022 ' + pr)
        npass = sum(1 for st in self.steps if st.ok)
        lines.append('')
        lines.append('单步重放：%d/%d 通过' % (npass, len(self.steps)))
        return '\n'.join(lines), npass, len(self.steps)



# ════════════════════════════════════ ② 并排伤害对照 ═════════════════════════════
#
# 一次攻击的三个数：
#   reverse —— 有出处的参照（反编译形状）
#   linear  —— 无出处的对照（外部实验表，量级超本作 TBYTE 上限）
#   actual  —— 内核**真实发出去**的 hp_chg，从线上帧里解出来的
#
# 为什么要并排：这两个模型在当前参数点上都算出 50/48/20，光看伤害数字分不出谁对。
# 但只要偏离标定参数（加防御、换等级、改破防），两者立刻分道扬镳——
# 并排摆出来，才能看出「我现在这个参数下，用两个模型各会打出多少」。
DamageRow = dict        # {who, target, atk, df, dp, lv, reverse, linear, actual, note}


def damage_row(atk, df, dp, lv, actual=None, who='', target='', note=''):
    """算一次攻击的三种伤害。actual 为 None 表示「还没实测」（纯推演行）。"""
    import damage_model as dm
    return {'who': who, 'target': target,
            'atk': atk, 'df': df, 'dp': dp, 'lv': lv,
            'reverse': dm.physical_damage(atk, df, dp, lv, model='reverse'),
            'linear': dm.physical_damage(atk, df, dp, lv, model='linear'),
            'actual': actual,
            'note': note}


def read_wstate(body):
    """解 WState(123) 帧体 → (id, hurt, life, ward, hp_chg)。

    ★ 位域按客户端源码定死的 MSB-first：bitfield = (hurt<<4)|(life<<2)|ward。
      这里与 tap 的拆法保持一致（同一事实，只拆一次——拆两遍就有两个真相）。
    """
    if len(body) < 6:
        raise ValueError('WState 帧体至少 6B，实得 %d' % len(body))
    wid, bf = body[0], body[1]
    chg = struct.unpack('<i', body[2:6])[0]
    return wid, (bf >> 4) & 0xF, (bf >> 2) & 0x3, bf & 0x3, chg


ARROW = '\u2192'      # →
BULLET = '\u203a'     # ›
WARN = '\u26a0'       # ⚠


def format_rows(rows, title='伤害对照'):
    """把 damage_row 列表排成人话表。actual 缺桩时打 ? 。"""
    out = ['== %s ==' % title,
           '%-16s %-14s %6s %6s %7s %7s'
           % ('谁->谁', '(A,D,P,Lt)', '参照', '对照', '实测', '三差')]
    warn = False
    for r in rows:
        params = '(%s,%s,%s,%s)' % (r['atk'], r['df'], r['dp'], r['lv'])
        who = '%s%s%s' % (r['who'], ARROW if r['who'] else '', r['target'])
        act = '?' if r['actual'] is None else str(r['actual'])
        if r['actual'] is None:
            diff = '?'
        else:
            d = r['actual'] - r['reverse']
            diff = '0' if d == 0 else '%+d' % d
        out.append('%-16s %-14s %6d %6d %7s %7s%s'
                   % (who, params, r['reverse'], r['linear'], act, diff,
                      ('   ' + BULLET + ' ' + r['note']) if r['note'] else ''))
        if r['actual'] is not None and r['actual'] != r['reverse']:
            warn = True
    out.append('')
    out.append('参照=reverse (有出处)   对照=linear (无出处，别当复刻)   '
               '实测=内核发出的 hp_chg')
    if warn:
        out.append(WARN + ' 有实测 != 参照：先看 note（浮动/加成/减半都会造成差），'
                   'note 也解释不了再去查 _dmg 实参')
    return '\n'.join(out)


def calibrate_rows():
    """当前内核参数点下的四行对照：主角->怪 / 宠物->怪 / 怪->主角 / 怪->宠物。

    直接读 m4_server 的常量而不是抄一遍：参数一旦被 world_config 覆盖，
    这里立刻跟着变，不会出现「编排器用旧参数对照新内核」。
    """
    import m4_server as m
    return [
        damage_row(m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL,
                   who='主角', target='敌方', note='标定点：录像值 50'),
        damage_row(m.PET_AT, m.ENEMY_DF, m.PET_DP, m.ENEMY_LEVEL,
                   who='宠物', target='敌方', note='标定点：录像值 48'),
        damage_row(m.ENEMY_AT, m.HERO_DF, m.ENEMY_DP, m.HERO_LEVEL,
                   who='敌方', target='主角', note='标定点：录像值 20'),
        damage_row(m.ENEMY_AT, m.PET_DF, m.ENEMY_DP, m.PET_LEVEL,
                   who='敌方', target='宠物', note='标定点：录像值 20'),
    ]



# ══════════════════════════════════ ③ 战斗编排器（两条腿合一）════════════════════
class BattleOracle(object):
    """在真实连接上跑一场战斗：逐步驱动 + 逐行对照。

    与 StepRunner 的分工：StepRunner 只管「帧序对不对」（结构），
    本类管「数值对不对」+ 把两者串成一次编排。
    """

    def __init__(self, runner, m4=None):
        self.r = runner
        self._m4 = m4
        self.units = {}        # wid -> {name, allied, hp}
        self.rows = []
        self.log = []

    def _m(self):
        if self._m4 is None:
            import m4_server as m
            self._m4 = m
        return self._m4

    @staticmethod
    def _parse_war_add(body):
        """解 WarAdd(115) → {id, name, allied, owner_id}。

        desc 是变长（GBK + \\0 结尾），所以第 4 个字节的 bitfield 位置不固定：
        从 offset=3 起找第一个 \\0 即为 desc 结束，其后依次是
        bitfield(1) owner(1) cls(1) grade(1)，name 从 offset+8 起到帧尾。

        allied 在 bitfield 的 bit7（MSB-first，见 build_war_add docstring）——
        这里**不能**自己拆一遍位域然后另写一套：同一事实只拆一次，
        与 m4_server.build_war_wstate / tap 的拆法保持一致。
        """
        if len(body) < 8:
            return None
        wid = body[0]
        z = body.find(b'\x00', 3)
        if z < 0 or z + 6 > len(body):
            return None
        bf_pos = z + 1
        bf = body[bf_pos]
        owner = body[bf_pos + 1]
        name_raw = body[bf_pos + 4:]
        name = '单位%d' % wid
        try:
            txt = name_raw.split(b'\x00')[0]
            if txt:
                name = txt.decode('gbk', 'replace')
        except Exception:                                       # noqa: BLE001
            pass
        return {'id': wid, 'name': name, 'allied': bool(bf & 0x80),
                'owner_id': owner, 'hp': None}

    # ── 从帧中吸收状态 ──
    def absorb(self, frames):
        """把一组帧吃进单位表；返回其中解出的 (wid, hp_chg) 列表。

        我方受击也一并返回 —— 调用方靠它补「怪打人」那一侧对照行。
        """
        rows = []
        for op, body in frames:
            if op == 112:
                self.units = {}
            elif op == 115:
                # 布局（build_war_add）：id(1) shape(2) desc(变长,GBK+\\0)
                #   bitfield(1) owner_id(1) cls(1) grade(1) name(变长,GBK+\\0)
                # desc 长度只能靠扫 \\0 定——desc 为空时也是 1B \\0。
                u = self._parse_war_add(body)
                if u:
                    self.units[u['id']] = u
            elif op == 116 and body:                       # WarDel：单位真退场
                self.units.pop(body[0], None)
            elif op == 123:
                wid, _hurt, life, _ward, chg = read_wstate(body)
                if life == 2:
                    self.units.pop(wid, None)             # 死亡：后续步骤别再算它
                rows.append((wid, chg))
        return rows

    def _uname(self, wid):
        u = self.units.get(wid)
        return u['name'] if u else '单位%d' % wid

    # ── 带对照的攻击步 ──
    @staticmethod
    def _expect_att(actor_kind, actor):
        """预期发起者 war_id：hero→100 / pet→250。actor 传了名字则用它反查。"""
        import m4_server as _m
        if actor_kind == 'hero':
            return _m.HERO_WAR_ID
        if actor_kind == 'pet':
            return _m.PET_WAR_ID
        return None

    def living_foes(self):
        """当前存活的敌方 id 列表（按 id 升序，确定性）。"""
        return sorted(w for w, u in self.units.items() if not u['allied'])

    def enemy_rows(self, st):
        """从一步的帧里挑出「敌方打我」的 WState，逐个产出对照行。

        为什么要单独抽出来：怪打人是公式的**另一侧**（ENEMY_AT/HERO_DF），
        它错了同样看不出来——「我明明一下都没挨打，血却在掉」这类问题
        只在并排表里能对上。
        """
        m = self._m()
        rows = []
        # ★ 敌方反击要看**整个回合**，不能只看到我方那一发为止。
        #   _bound_step 截在「我方 119 之后、下一个 117 之前」——
        #   而敌方反击恰好排在那之后（我方出手 → 敌方逐个还手），
        #   用它来切就把全部反击切掉了，并排表只剩「我打人」那一半。
        #   真正的截断点：下一轮的**我方**攻击（回合已经翻篇）。
        mine = {self._expect_att('hero', 'hero'), self._expect_att('pet', 'pet')}
        # 截断点 = 下一轮的我方攻击。
        # 判据：WarTurn(114) 由 _resolve_hero_round 在**整回合**结算完时发，
        # 所以「114 之后第一个我方链」就是下一轮；它之前的都归本步。
        bound = len(st.frames)
        turn_at = next((i for i, (op, _b) in enumerate(st.frames)
                        if op == 114), None)
        chains = attack_chains(st.frames)
        if turn_at is not None:
            for chain in chains:
                if chain['i'] > turn_at and chain['att'] in mine:
                    bound = chain['i']
                    break
        else:
            # 没有 114（窗口把回合截断了）：退化为「第一个我方链之后的部分」，
            # 只保留紧跟其后的敌方链，遇到第二个我方链就停。
            first_mine = next((c['i'] for c in chains if c['att'] in mine),
                              None)
            if first_mine is not None:
                for chain in chains:
                    if chain['i'] > first_mine and chain['att'] in mine:
                        bound = chain['i']
                        break
        chains = attack_chains(st.frames[:bound])
        for chain in chains:
            if chain['att'] is None or chain['att'] in mine:
                continue
            wid = chain['vic']
            if chain['chg'] is None:
                continue
            chg = chain['chg']
            ward = 2 if chain.get('defend') else 1
            u = self.units.get(wid) or {}
            if u.get('allied') is not True:
                continue
            if wid == m.HERO_WAR_ID:
                df, lv, target_name = m.HERO_DF, m.HERO_LEVEL, '主角'
            else:
                df, lv, target_name = m.PET_DF, m.PET_LEVEL, '宠物'
            base = chg
            note = '防御减半已生效（已反推原始伤害）' if ward == 2 else ''
            if ward == 2:
                base = chg * m.DEFEND_DAMAGE_DIVISOR
            r = damage_row(m.ENEMY_AT, df, m.ENEMY_DP, lv, actual=base,
                           who='敌方', target=target_name, note=note)
            r['step'] = st
            r['raw_chg'] = chg
            r['defending'] = (ward == 2)
            rows.append(r)
        return rows

    def attack_step(self, actor, target, actor_kind, c2s_op, c2s_body,
                    window=0.7, note='', auto_target=True):
        """发一次攻击，按 actor_kind 选公式参数，产出并排对照行。

        actor_kind: 'hero' | 'pet'（'enemy' 由敌方自己发，不走本方法）
        target=None + auto_target → 自动打首个存活敌方。
        """
        m = self._m()
        # 本次指令的目标（C2S 普攻的 body 首字节就是 target id）
        want_vic = None
        if c2s_op and c2s_body:
            want_vic = c2s_body[0]
        elif target is not None:
            want_vic = target
        if target is None and auto_target:
            alive = self.living_foes()
            target = alive[0] if alive else None
            if want_vic is None:
                want_vic = target
        # 战斗已结束还发攻击指令：显式记为失败，别默认发出去再事后猜
        if not self.r.in_war:
            st = Step('hero_attack' if actor_kind == 'hero' else 'pet_attack',
                      '%s 普攻 %s' % (actor, target), c2s_op, c2s_body, (),
                      note='战斗已结束，未发指令')
            st.problems.append('战斗已结束却仍发起攻击指令')
            st.ok = False
            self.r.steps.append(st)
            row = damage_row(m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL,
                             actual=None, who=actor, target=str(target),
                             note='战斗已结束')
            row['step'] = st
            self.rows.append(row)
            return row
        st = self.r.step('hero_attack' if actor_kind == 'hero' else 'pet_attack',
                        '%s 普攻 %s' % (actor, target),
                        c2s_op, c2s_body, note, window)
        self.absorb(st.frames)
        # 步边界与攻击链切分走模块级 helper（attack_chains / _bound_step）：
        # 我方伤害与敌方反击必须用**同一套**切法，否则同一帧会被两种规则
        # 解释成两个意思——「同一事实两个真相」这个坑本项目已经踩过一次。
        bound, _first = _bound_step(st.frames)
        # ★ 允许「我方任一单位」的那一条，但**发起者必须真的是预期角色**；
        #   如果首个链是宠物（速度序让它先动），单独记一行并照样对照——
        #   那是真实帧序，编排器的职责是**如实呈现**，不是把它挑漂亮。
        #   但公式参数必须跟实际发起者走：拿 hero 的参数去对 pet 的伤害，
        #   永远差 2（50 vs 48），而这种「差一点」最容易被当成浮云忽略过去。
        want_att = self._expect_att(actor_kind, actor)
        chains = attack_chains(st.frames[:bound])
        mine_ids = {self._expect_att('hero', 'hero'),
                    self._expect_att('pet', 'pet')}
        chg = None
        picked = None
        slipped = None
        for chain in chains:
            if chain['att'] is None:
                continue
            if want_vic is not None and chain['vic'] != want_vic:
                continue
            if chain['att'] not in mine_ids:
                continue          # 敌方反击那几条不归我方
            if chain['att'] == want_att:
                picked = chain
                chg = chain['chg']
                break
            if slipped is None:
                slipped = chain   # 我方另一个单位抢先了（速度序）
        if picked is None and slipped is not None:
            picked = slipped
            chg = slipped['chg']
            actor_kind = ('pet' if slipped['att'] == self._expect_att('pet', 'pet')
                          else 'hero')
            actor = '主角' if actor_kind == 'hero' else '宠物'
            st.note = (st.note + ' ' if st.note else '') + ('速度序让 %s 先出手，本行按实际发起者对照' % actor)
        if picked is None and chains:
            _atts = sorted({c['att'] for c in chains if c['att'] is not None})
            st.note = (st.note + ' ' if st.note else '') + ('本步没有 %s(att=%s) 打 %s 的链（实际发起者 %r）' % (actor, want_att, target, _atts))
        elif picked is not None and picked['chg'] is None:
            st.note = (st.note + ' ' if st.note else '') + '命中的攻击链没有下发 WState（客户端只播动作不飘字）'
        if actor_kind == 'hero':
            atk, df, dp, lv = m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL
            actor = '主角'
        else:
            atk, df, dp, lv = m.PET_AT, m.ENEMY_DF, m.PET_DP, m.ENEMY_LEVEL
            actor = '宠物'
        row = damage_row(atk, df, dp, lv, actual=chg,
                         who=actor, target=str(target),
                         note=note or st.label)
        row['step'] = st
        row['chain'] = picked
        self.rows.append(row)
        # 敌方反击也进同一张表：并排对照要**两侧**都看得见，
        # 只放「我打人」那一侧等于只用了一半的公式。
        self.rows.extend(self.enemy_rows(st))
        return row

    def enemy_rows(self, st):
        """从一步的帧里挑出「敌方打我」的 WState，逐个产出对照行。

        为什么要单独抽出来：怪打人是公式的**另一侧**（ENEMY_AT/HERO_DF），
        它错了同样看不出来——「我明明一下都没挨打，血却在掉」这类问题
        只在并排表里能对上。
        """
        m = self._m()
        rows = []
        bound, _first = _bound_step(st.frames)
        # 只取**敌方发起**的链（att_id 不是我方单位）
        mine = {self._expect_att('hero', 'hero'), self._expect_att('pet', 'pet')}
        for chain in attack_chains(st.frames[:bound]):
            if chain['att'] is None or chain['att'] in mine:
                continue
            wid = chain['vic']
            if chain['chg'] is None:
                continue
            u = self.units.get(wid) or {}
            if u.get('allied') is not True:
                continue
            chg = chain['chg']
            if wid == m.HERO_WAR_ID:
                df, lv, target_name = m.HERO_DF, m.HERO_LEVEL, '主角'
            else:
                df, lv, target_name = m.PET_DF, m.PET_LEVEL, '宠物'
            r = damage_row(m.ENEMY_AT, df, m.ENEMY_DP, lv, actual=chg,
                           who='敌方', target=target_name,
                           note='敌 方 反 击'.replace(' ', ''))
            r['step'] = st
            r['raw_chg'] = chg
            rows.append(r)
        return rows

    def end_step(self, label='战斗结束', window=0.9):
        """记一步 war_end（委托给 runner）。"""
        return self.r.end_step(label, window)

    def report(self):
        steps_txt, npass, ntotal = self.r.report()
        return (steps_txt + '\n\n' + format_rows(self.rows, '战斗伤害对照'),
                npass, ntotal)
