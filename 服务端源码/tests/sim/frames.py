# -*- coding: utf-8 -*-
"""战斗帧的**单一字段定义** + 由定义生成的收发包逻辑。

为什么单独一个模块
------------------
战斗帧的字段此前有三份实现，靠人肉保持一致：

  1. `m4_server.build_war_*`   —— 逐帧手写 `struct.pack`（发包）
  2. `m4_server` 的分派/`_finish_pet_turn` —— 逐帧手写 `body[0]`、`struct.unpack`
     （收包，且**没有任何一处**与发包共用定义）
  3. `tap.OVERLAY`             —— 调试台解码用的字段规格串（第三份）

三份各自漂移过一次（166 的条件字段、169 的缺 id、115/123 的位序），
所以这里把**字段定义**收敛成一份 `SPEC`，发包与收包都由它生成：

    frames.pack_s2c('S2CWarWState', id=1, hurt=1, life=0, ward=1, hp_chg=50)
    frames.unpack_c2s(169, body)     # → {'target': 2, 'perform': 415}

调试台的解码规格也由这里派生（`tap_specs()`），`tap.py` 直接合并，不再各写一份。

规格串语法（与 `tap.OVERLAY` 同形，多两个构造）
----------------------------------------------
    name:N     无符号小端 N 字节（N = 1/2/4）
    name:Ns    有符号小端 N 字节（如 hp_chg:4s）
    name:s     C 字符串：GBK 字节 + \\0（收包时无 \\0 则吃到帧尾，对齐 op175）
    name:fN    定长 N 字节串（不足补 \\0）
    name:D     UnpackDesc 标签联合：空值 = 单字节 0x00
    name:T     吃剩余字节（vic_ids 这类 1B×N 列表）
    name:B(子名:最高位:位宽, …)   MSB-first 位域，整字节落盘
              例 S2CWarWState 的 bf:B(hurt:7:4,life:3:2,ward:1:2)
                 → 线上字节 = (hurt<<4) | (life<<2) | ward
              （位序由 mxmain.dll UnpackBit 核心 0x41A4D0 实证：off=0 → bit7）
    if(表达式)  之后字段的条件开关，表达式形如 `op==0`（只支持 `名字==整数`）

值域约定
--------
- 位域既可按整字节传（`bf=0x80`），也可按子名传（`allied=1`）；子名优先。
- `T` 收 bytes / int 序列；解出后统一给 bytes（列表请自行 `list()`）。
- 未登记 opcode / 帧体长度不足 → `unpack_*` 返回 None（调用方保留原有兜底分支）。

证据等级
--------
字段定义本身是〔B⁺〕（`客户端源码/src/netwar.pyc` 代码对象逐条反编译 + 真机日志），
本模块只负责让它**只有一份**。
"""
import struct

__all__ = ['SPEC', 'S2C_OPS', 'C2S_OPS', 'frame_name', 'pack_s2c', 'pack_c2s',
           'unpack_s2c', 'unpack_c2s', 'parse_spec', 'tap_specs']


# ══════════════════════════════════════════════════════════ 唯一字段定义 ══
# (opcode, 方向, 规格串)
#   方向 S2C = 服务器→客户端（发包）；C2S = 客户端→服务器（收包）
#   同 opcode 在两个方向是**独立编号空间**，故按方向分开查。
SPEC = {
    # ── S2C：战斗（netwar.S2CWar*；字段序见 协议资料/规格/M4_协议规格.md §3）──
    'S2CWarStart': (112, 'S2C', 'scene:2 alineup:1 elineup:1 wtype:1 x:2 y:2'),
    'S2CWarEnd': (113, 'S2C', ''),
    'S2CWarTurn': (114, 'S2C', 'lastcmd:1'),
    # bf：allied=bit7 / type=bit6 / child=bit5，**MSB-first**（写 0x80 才是「我方」）
    'S2CWarAdd': (115, 'S2C', 'id:1 shape:2 desc:D '
                              'bf:B(allied:7:1,type:6:1,child:5:1) '
                              'owner_id:1 cls:1 grade:1 name:s'),
    'S2CWarDel': (116, 'S2C', 'id:1'),
    'S2CWarAttackNormal': (117, 'S2C', 'att_id:1 vic_id:1'),
    'S2CWarAttackNormalEnd': (119, 'S2C', 'id:1'),
    'S2CWarProtect': (121, 'S2C', 'protector_id:1 protege_id:1'),
    'S2CWarPerform': (122, 'S2C', 'id:1 pid:2 vic_ids:T'),
    # bf：hurt=bit7-4 / life=bit3-2 / ward=bit1-0；hp_chg **正数=扣血**
    'S2CWarWState': (123, 'S2C', 'id:1 bf:B(hurt:7:4,life:3:2,ward:1:2) hp_chg:4s'),
    'S2CWarHPPercent': (125, 'S2C', 'id:1 per:1'),
    # 只对友方发（敌方没有 m_HPFull 血条对象）
    'S2CWarSummon': (129, 'S2C', 'id:1 op:1'),

    # ── C2S：战斗指令 160–177（netwar.C2SWar*；见 M4 规格 §3.4）──
    'C2SWarAttack': (160, 'C2S', 'id:1'),                 # id = 目标
    'C2SWarPerform': (161, 'C2S', 'id:1 perform:2'),      # id = 施法者
    'C2SWarSpecial': (162, 'C2S', 'id:1 sid:2'),
    'C2SWarDefend': (163, 'C2S', ''),
    'C2SWarEscape': (164, 'C2S', ''),
    'C2SWarItem': (165, 'C2S', 'id:1 item:4'),
    # ★ 166 的 id 是**条件字段**：op==0（放出）才写 4B；op==1（召回）只有 1 字节
    'C2SWarSummon': (166, 'C2S', 'op:1 if(op==0) id:4'),
    'C2SWarProtect': (167, 'C2S', 'id:1'),
    'C2SWarSummAttack': (168, 'C2S', 'id:1 target:1'),
    # ★ 169 的 id 形参**不上线**：body 只有 target(1)+perform(2)
    'C2SWarSummPerform': (169, 'C2S', 'target:1 perform:2'),
    'C2SWarSummGuard': (170, 'C2S', 'id:1'),
    'C2SWarSummItem': (171, 'C2S', 'id:1 target:1 item:4'),
    'C2SWarSummProtect': (172, 'C2S', 'id:1 target:1'),
    'C2SWarCatch': (173, 'C2S', 'id:1'),
    'C2SWarSummEscape': (174, 'C2S', 'id:1'),
    # 常量名为 C2S_WAR_TEAM，实际是战斗喊话/签名（内容形如 '#heroID@targetID'）
    'C2SSendSign': (175, 'C2S', 'msg:s'),
    # 176 两个发送函数（C2SWarLook / C2SLookWarrior），线上都是 1 字节单位 id
    'C2SWarLook': (176, 'C2S', 'id:1'),
    'C2SChild': (177, 'C2S', ''),
}

S2C_OPS = {v[0]: k for k, v in SPEC.items() if v[1] == 'S2C'}
C2S_OPS = {v[0]: k for k, v in SPEC.items() if v[1] == 'C2S'}
# 同 opcode 多函数（176 = C2SWarLook / C2SLookWarrior，布局相同）→ 别名
ALIASES = {'C2SLookWarrior': 'C2SWarLook'}


def frame_name(direction, op):
    """opcode → 帧名；未登记返回 None。"""
    table = S2C_OPS if str(direction).upper() == 'S2C' else C2S_OPS
    return table.get(int(op))


def _spec_of(name):
    name = ALIASES.get(name, name)
    if name not in SPEC:
        raise KeyError('未登记的帧名：%s' % name)
    return SPEC[name]


# ══════════════════════════════════════════════════════════ 规格串解析 ══
def parse_spec(spec):
    """规格串 → [{'kind','name','size','bits','gate'}...]。

    `gate` 是本字段的条件（None = 无条件），供 `if(...)` 之后的所有字段继承，
    直到出现新的 `if(...)`/`else`。本模块只需要单条件（166），故不做嵌套。
    """
    out, gate = [], None
    for tok in (spec or '').split():
        if tok.startswith('if(') and tok.endswith(')'):
            gate = _parse_gate(tok[3:-1])
            continue
        if tok == 'else':
            gate = ('else', gate)
            continue
        if ':' not in tok:
            continue
        name, typ = tok.split(':', 1)
        if typ.startswith('B('):
            out.append({'kind': 'B', 'name': name, 'gate': gate,
                        'bits': _parse_bits(typ[2:-1])})
        elif typ == 'T':
            out.append({'kind': 'T', 'name': name, 'gate': gate})
        elif typ == 'D':
            out.append({'kind': 'D', 'name': name, 'gate': gate})
        elif typ == 's':
            out.append({'kind': 's', 'name': name, 'gate': gate})
        elif typ.startswith('f'):
            out.append({'kind': 'f', 'name': name, 'gate': gate,
                        'size': int(typ[1:])})
        elif typ.endswith('s'):
            out.append({'kind': 'sint', 'name': name, 'gate': gate,
                        'size': int(typ[:-1])})
        else:
            out.append({'kind': 'uint', 'name': name, 'gate': gate,
                        'size': int(typ)})
    return out


def _parse_gate(expr):
    """`op==0` → ('op', 0)。"""
    name, _, val = expr.partition('==')
    return (name.strip(), int(val.strip()))


def _parse_bits(inner):
    """`hurt:7:4,life:3:2` → [('hurt', 7, 4), ...]（最高位, 位宽）。"""
    bits = []
    for part in inner.split(','):
        part = part.strip()
        if not part:
            continue
        nm, hi, width = part.split(':')
        bits.append((nm.strip(), int(hi), int(width)))
    return bits


def _gate_ok(gate, values):
    if gate is None:
        return True
    if gate[0] == 'else':            # 本模块未用到，保守返回 False
        return False
    name, want = gate
    return int(values.get(name, 0)) == want


# ══════════════════════════════════════════════════════ 发包（S2C/C2S）══
def pack_body(frame, values):
    """按字段定义把 values 打成帧体（不含帧头）。"""
    _op, _dir, spec = _spec_of(frame)
    out = bytearray()
    for f in parse_spec(spec):
        if not _gate_ok(f['gate'], values):
            continue
        out += _enc_field(f, values)
    return bytes(out)


def pack_s2c(frame, **values):
    """S2C 帧体（调用方用 `make_s2c` 套帧头/压缩）。"""
    return pack_body(frame, values)


def pack_c2s(frame, **values):
    """C2S 帧体（自测/注入用；服务端不主动发）。"""
    return pack_body(frame, values)


def _enc_field(f, values):
    kind, nm = f['kind'], f['name']
    if kind == 'uint':
        return int(values.get(nm, 0)).to_bytes(f['size'], 'little')
    if kind == 'sint':
        return int(values.get(nm, 0)).to_bytes(f['size'], 'little', signed=True)
    if kind == 'B':
        return bytes([_enc_bits(f, values)])
    if kind == 's':
        s = values.get(nm, '')
        if isinstance(s, str):
            s = s.encode('gbk', 'replace')
        return bytes(s) + b'\x00'
    if kind == 'f':
        s = values.get(nm, '')
        if isinstance(s, str):
            s = s.encode('gbk', 'replace')
        return bytes(s)[:f['size']].ljust(f['size'], b'\x00')
    if kind == 'D':
        v = values.get(nm, None)
        if v is None:
            return b'\x00'
        if isinstance(v, int):
            return bytes([v])
        return bytes(v)
    if kind == 'T':
        v = values.get(nm, b'')
        if isinstance(v, int):
            return bytes([v])
        return bytes(bytearray(v))
    raise ValueError('未知字段类型：%r' % (f,))


def _enc_bits(f, values):
    byte = int(values.get(f['name'], 0) or 0)
    for (sub, hi, width) in f['bits']:
        if sub in values:
            v = int(values[sub] or 0) & ((1 << width) - 1)
            shift = hi - width + 1
            byte = (byte & ~(((1 << width) - 1) << shift)) | (v << shift)
    return byte


# ══════════════════════════════════════════════════════════ 收包（C2S）══
def unpack_c2s(op, body):
    """C2S 帧体 → dict；未登记或长度不足返回 None。"""
    return _unpack('C2S', op, body)


def unpack_s2c(op, body):
    """S2C 帧体 → dict；未登记或长度不足返回 None（自测/调试台用）。"""
    return _unpack('S2C', op, body)


def _unpack(direction, op, body):
    name = frame_name(direction, op)
    if name is None:
        return None
    _op, _dir, spec = _spec_of(name)
    data = bytes(body or b'')
    cur, out = 0, {}
    for f in parse_spec(spec):
        if not _gate_ok(f['gate'], out):
            continue
        got = _dec_field(f, data, cur, out)
        if got is None:
            return None                      # 截断：交给调用方的兜底分支
        val, cur = got
        out.update(val)
    out['_len'] = cur                        # 吃掉的字节数（T 字段会等于 len(data)）
    return out


def _dec_field(f, data, cur, out):
    kind, nm = f['kind'], f['name']
    if kind in ('uint', 'sint'):
        n = f['size']
        if cur + n > len(data):
            return None
        raw = data[cur:cur + n]
        v = int.from_bytes(raw, 'little', signed=(kind == 'sint'))
        return {nm: v}, cur + n
    if kind == 'B':
        if cur + 1 > len(data):
            return None
        byte = data[cur]
        res = {nm: byte}
        for (sub, hi, width) in f['bits']:
            shift = hi - width + 1
            res[sub] = (byte >> shift) & ((1 << width) - 1)
        return res, cur + 1
    if kind == 's':
        end = data.find(b'\x00', cur)
        if end < 0:                          # op175：PacketAddS(msg,len) 不带 \0
            end = len(data)
        return {nm: data[cur:end].decode('gbk', 'replace')}, end + 1 if end < len(data) else end
    if kind == 'f':
        n = f['size']
        if cur + n > len(data):
            return None
        raw = data[cur:cur + n].rstrip(b'\x00')
        return {nm: raw.decode('gbk', 'replace')}, cur + n
    if kind == 'D':
        # UnpackDesc：1B 条目数 n；n==0 → 空（1 字节）。非空时本模块只吃 1B，
        # 战斗里 desc 恒为空，非空场景由调用方自解（M4 规格 §3 明确要求发空 desc）。
        if cur + 1 > len(data):
            return None
        n = data[cur]
        if n == 0:
            return {nm: b''}, cur + 1
        return None
    if kind == 'T':
        return {nm: data[cur:]}, len(data)
    return None


# ══════════════════════════════════════════════════ 调试台规格派生（tap）══
# tap 的 DSL 不支持位域位宽（只有单 bit 的 B(...)），故这两条做等价翻译；
# 其余 S2C 战斗帧直接同串。派生后 tap 覆盖表不再手写这 12 条。
_TAP_SPEC_OVERRIDE = {
    115: 'id:1 shape:2 desc:D bf:B(allied=7,type=6,child=5) owner_id:1 cls:1 grade:1 name:s',
    123: 'id:1 hurt_life_ward:1 hp_chg:4s',
    122: 'id:1 pid:2 vic_ids:T',
}


def tap_specs():
    """战斗 S2C 帧 → tap.OVERLAY 规格串（{op: (帧名, 规格)}）。

    键与 tap.OVERLAY 一致；`_OV_NOTE` 里的语义提示仍留在 tap 侧（那是给人看的备注，
    不是字段定义）。
    """
    out = {}
    for name, (op, direction, spec) in SPEC.items():
        if direction != 'S2C':
            continue
        out[op] = (name, _TAP_SPEC_OVERRIDE.get(op, spec))
    return out


# ══════════════════════════════════════════════════════════════ 自测 ══
def _selftest():
    """最小往返自检：python frames.py"""
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('OK  ' if cond else 'FAIL') + ' ' + msg)
        ok = ok and cond

    # WState：位域按 MSB-first 组字节，hp_chg 有符号正数
    b = pack_s2c('S2CWarWState', id=1, hurt=1, life=0, ward=1, hp_chg=50)
    check(b == b'\x01\x11\x32\x00\x00\x00', 'WState 打包 %s' % b.hex(' '))
    d = unpack_s2c(123, b)
    check(d['hurt'] == 1 and d['life'] == 0 and d['ward'] == 1 and d['hp_chg'] == 50,
          'WState 解包 %r' % d)

    # WarAdd：allied 在 bit7
    b = pack_s2c('S2CWarAdd', id=0, shape=4005, desc=None, allied=1,
                 owner_id=0, cls=0, grade=1, name='你')
    check(b[4] == 0x80, 'WarAdd allied=0x80（MSB，id1+shape2+desc1 之后）')
    d = unpack_s2c(115, b)
    check(d['allied'] == 1 and d['name'] == '你' and d['shape'] == 4005,
          'WarAdd 解包 %r' % d)

    # Perform：vic_ids 吃剩余
    b = pack_s2c('S2CWarPerform', id=0, pid=1101, vic_ids=[1, 2])
    check(b == b'\x00\x4d\x04\x01\x02', 'Perform 打包 %s' % b.hex(' '))
    check(list(unpack_s2c(122, b)['vic_ids']) == [1, 2], 'Perform 解包 vic_ids')

    # 166 条件字段：放出 5B / 召回 1B
    check(pack_c2s('C2SWarSummon', op=0, id=1001) == b'\x00\xe9\x03\x00\x00', '166 放出 5B')
    check(pack_c2s('C2SWarSummon', op=1) == b'\x01', '166 召回 1B')
    check(unpack_c2s(166, b'\x01') == {'op': 1, '_len': 1}, '166 召回解包')

    # 169 无 id
    b = pack_c2s('C2SWarSummPerform', target=2, perform=415)
    check(b == b'\x02\x9f\x01', '169 打包 3B（无 id）')
    check(unpack_c2s(169, b)['perform'] == 415, '169 解包')

    # 175 无 \0 的变长串
    check(unpack_c2s(175, b'#1@2')['msg'] == '#1@2', '175 无 NUL 变长串')

    # 未登记 / 截断
    check(unpack_c2s(199, b'') is None, '未登记 opcode → None')
    check(unpack_c2s(169, b'\x01') is None, '截断 → None')

    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
