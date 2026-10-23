# -*- coding: utf-8 -*-
"""协议级测试：宠物三帧（S2C 48/49/50/51）+ C2S 129/131。

在 127.0.0.1:3138 起 m4_server 实例，脚本客户端走：
  Hello → 登录 → 建角 → 选角进入世界（应收到 SummonInfo + SummonSkill）
  → C2S_SUMM_VIEW(129) → 校验 S2CPropView(51) 资质/抗性 + SummonSkill(49)
  → C2S_SUMM_SET_FIGHT(131) → 校验 S2CSetFight(50)
"""
import socket
import struct
import sys
import time

HOST, PORT = '127.0.0.1', 3138
sys.path.insert(0, 'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m  # noqa

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s %s' % ('PASS' if ok else 'FAIL', name, detail))


def recv_exact(c, n, timeout=3.0):
    c.settimeout(timeout)
    buf = b''
    while len(buf) < n:
        ch = c.recv(n - len(buf))
        if not ch:
            raise ConnectionError('closed')
        buf += ch
    return buf


def recv_s2c(c, timeout=3.0):
    n = recv_exact(c, 1, timeout)[0]
    if n == 0xFF:
        n = struct.unpack('<H', recv_exact(c, 2, timeout))[0]
    payload = recv_exact(c, n, timeout)
    return payload[0], payload[1:]


def send_c2s(c, op, body=b''):
    c.sendall(bytes([op]) + bytes([len(body)]) + body)


def hello_resp(seed):
    obf = ((seed + 0x100000000) % 3227993 + 2562088) ^ 6636321
    return bytes([obf & 0xFF, (obf >> 8) & 0xFF, (obf >> 16) & 0xFF]) + b'\x00' * 22


def login_body(name='100860001', pwd='abcdef'):
    b = bytearray()
    b += bytes([0, 10]) + struct.pack('<H', 43)
    b += name.encode('gbk')[:32].ljust(32, b'\x00')
    b += pwd.encode('gbk')[:20].ljust(20, b'\x00')
    b += bytes([1, 2]) + struct.pack('<H', 73)
    return bytes(b)


def collect(c, want, window=1.0, maxf=80):
    """收集帧直到出现 want 的 opcode（或超时），返回全部帧。"""
    frames = []
    try:
        for _ in range(maxf):
            op, data = recv_s2c(c, window)
            frames.append((op, data))
            if op == want:
                break
    except (socket.timeout, ConnectionError):
        pass
    return frames


def main():
    c = socket.create_connection((HOST, PORT), timeout=5)
    op, data = recv_s2c(c)
    seed = struct.unpack('<I', data[:4])[0]
    c.sendall(hello_resp(seed))
    time.sleep(0.2)
    collect(c, 999, 0.3)
    send_c2s(c, 1, login_body())
    time.sleep(0.2)
    collect(c, 999, 0.3)
    send_c2s(c, 3, '宠物测试'.encode('gbk')[:20].ljust(20, b'\x00') + b'\x00' * 5)
    time.sleep(0.3)
    collect(c, 999, 0.3)
    send_c2s(c, 2, struct.pack('<I', 1) + b'\x00')
    frames = collect(c, m.OP_SUMMON_SKILL, 2.0)

    # ── 进世界应带宠物帧 ──
    info = [d for o, d in frames if o == m.OP_SUMMON_INFO]
    skills = [d for o, d in frames if o == m.OP_SUMMON_SKILL]
    check('进世界下发 SummonInfo(48)', len(info) == 1, 'n=%d' % len(info))
    if info:
        d = info[0]
        pid = struct.unpack('<I', d[:4])[0]
        name = d[4:24].rstrip(b'\x00').decode('gbk')
        shape = struct.unpack('<H', d[24:26])[0]
        desc_n = d[26]
        o = 27 + desc_n
        grade = d[o]
        vals = struct.unpack('<IIIIII', d[o + 1:o + 25])       # HP,MP,FP,HPMax,MPMax,FPMax
        atk, df, spd, magdef = struct.unpack('<IIII', d[o + 25:o + 41])
        strs = struct.unpack('<HHHHHH', d[o + 41:o + 53])
        exp = struct.unpack('<II', d[o + 53:o + 61])
        need, life = struct.unpack('<II', d[o + 61:o + 69])
        molog = d[o + 69]
        ok = (pid == m.PET_ID and name == m.PET_NAME and shape == m.PET_SHAPE
              and grade == m.PET_LEVEL and vals[0] == m.PET_HP and vals[1] == m.PET_MP
              and vals[2] == m.PET_FP and vals[3] == m.PET_HP and vals[4] == m.PET_MP
              and vals[5] == m.PET_FP and (atk, df, spd, magdef) ==
              (m.PET_DAMAGE, m.PET_DEFENSE, m.PET_SPEED, m.PET_MAGDEF)
              and strs == (m.PET_STR, m.PET_CON, m.PET_MAG, m.PET_CHA, m.PET_DEX, m.PET_POINT)
              and exp == (m.PET_EXP, m.PET_EXPNEXT) and need == 0 and life == m.PET_LIFE
              and molog == 0 and len(d) == o + 70)
        check('SummonInfo 字段逐一校验', ok,
              'pid=%d name=%s shape=%d grade=%d HP=%d 攻击=%d 速度=%d 寿命=%d len=%d'
              % (pid, name, shape, grade, vals[0], atk, spd, life, len(d)))
    check('进世界下发 SummonSkill(49)', len(skills) == 1, 'n=%d' % len(skills))
    if skills:
        d = skills[0]
        pid = struct.unpack('<I', d[:4])[0]
        n, x = d[4], d[5]
        ids = list(struct.unpack('<%dH' % n, d[6:6 + 2 * n]))
        check('SummonSkill 字段校验', pid == m.PET_ID and n == len(m.PET_SKILLS)
              and x == m.PET_HF_FROM and ids == m.PET_SKILLS,
              'pid=%d n=%d x=%d ids=%s' % (pid, n, x, ids))

    # ── C2S 129 查看 → S2C 51 资质/抗性 ──
    send_c2s(c, m.C2S_SUMM_VIEW, struct.pack('<I', m.PET_ID))
    frames = collect(c, m.OP_SUMMON_SKILL, 2.0)
    pv = [d for o, d in frames if o == m.OP_PROP_VIEW]
    check('C2S_SUMM_VIEW(129) → S2CPropView(51)', len(pv) == 1, 'n=%d' % len(pv))
    if pv:
        d = pv[0]
        pid = struct.unpack('<I', d[:4])[0]
        extra = struct.unpack('<H', d[4:6])[0]
        ab = struct.unpack('<HHHHH', d[6:16])
        flag, umax = struct.unpack('<II', d[16:24])
        prozz = d[24]
        promax = struct.unpack('<H', d[25:27])[0]
        res = struct.unpack('<bbbbbbbbb', d[27:36])
        ok = (pid == m.PET_ID and extra == 0 and ab == m.PET_ABILITY
              and flag == 0 and umax == 0 and prozz == 0 and promax == 0
              and res == m.PET_RESIST and len(d) == 36)
        check('PropView 资质/抗性字段校验', ok,
              'pid=%d 资质=%s 抗性=%s len=%d' % (pid, ab, res, len(d)))

    # ── C2S 131 出战 → S2C 50 ──
    send_c2s(c, m.C2S_SUMM_SET_FIGHT, struct.pack('<I', m.PET_ID))
    frames = collect(c, m.OP_SUMMON_FIGHT, 2.0)
    sf = [d for o, d in frames if o == m.OP_SUMMON_FIGHT]
    ok = len(sf) == 1 and struct.unpack('<I', sf[0][:4])[0] == m.PET_ID
    check('C2S_SUMM_SET_FIGHT(131) → S2CSetFight(50)', ok, 'n=%d' % len(sf))

    c.close()
    print('')
    bad = [n for n, ok in results if not ok]
    print('==== %d/%d 通过 ====' % (len(results) - len(bad), len(results)))
    if bad:
        print('未过: %s' % bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
