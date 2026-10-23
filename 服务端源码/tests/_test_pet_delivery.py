# -*- coding: utf-8 -*-
"""宠物交付闭环：实例唯一性 + 交付原子性（P1，进程内，假连接，tmp 存档）。

硬性约束落地（用户 2026-10-23 提出）：
  · 不得重复创建宠物实例 —— pid 严格唯一（单调计数器 + 显式判重 + 读档去重）
  · 不可逆交付必须原子 —— 仅在持久化成功后才确认（发帧）；失败回滚并如实
    上报，**一帧都不发**，严禁误报成功

覆盖九组：
  ① 造型合法性：目录外 shape / 非整数 / None → 拒收，不落盘不发帧
  ② 等级钳制：0/负数 → 1；超大 → PET_LEVEL_MAX
  ③ 栏位上限：PET_SLOT_MAX 只后拒收（reason=full）
  ④ pid 唯一性：连续入栏不重复、pet_seq 与 max(pid) 一致
  ⑤ 读档去重：存档里重复 pid 只保留首条；非法造型回落目录默认
  ⑥ 原子性·同步落盘失败：save_role→False → 回滚 + 零帧 + reason=persist_failed
  ⑦ 原子性·异步落盘失败：同样回滚 + 零帧（flush 后取 _save_last_ok）
  ⑧ 存档被禁（原文件损坏）：reason=save_blocked，不写不发
  ⑨ 成功路径：48/49/53 帧序齐全、磁盘含该宠、重登读回、错误位清零

用法：python _test_pet_delivery.py
"""
from __future__ import print_function

import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')
if not os.path.isdir(SIM):
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
if SIM not in sys.path:
    sys.path.insert(0, SIM)

import m4_server as m                       # noqa: E402

ok = True


def check(c, msg, detail=''):
    global ok
    if detail and not c:
        msg += '  ← %r' % (detail,)
    print(('OK  ' if c else 'FAIL') + ' ' + msg)
    ok = ok and bool(c)


class FakeConn(object):
    def __init__(self):
        self.sent = []

    def sendall(self, b):
        self.sent.append(bytes(b))

    def ops(self, since=0):
        out = []
        for raw in self.sent[since:]:
            n = raw[0]
            raw = raw[1:]
            assert n == len(raw), '帧长度不符'
            out.append((raw[0], raw[1:]))
        return out


_seq = [0]


def make_server(tmp, save_async=False, tag=None):
    _seq[0] += 1
    tag = tag or ('d%d' % _seq[0])
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'log_' + tag),
                     savedir=os.path.join(tmp, 'save_' + tag))
    srv._arm_cmd_timer = lambda: None
    srv._save_async = save_async
    if save_async:
        srv._ensure_save_writer()
    return srv


def disk_pets(path):
    with open(path, 'r', encoding='utf-8') as f:
        sv = json.load(f)
    return {int(e.get('pid')): e for e in (sv.get('pets') or [])}


def fill_slots(srv, conn=None):
    """把宠物栏填到上限（走真实 _add_pet，确保每只都已落盘确认）。"""
    out = []
    shapes = sorted(m.PET_CATALOG)
    while len(srv.pets) < m.PET_SLOT_MAX:
        p = srv._add_pet(conn, shapes[len(out) % len(shapes)])
        if p is None:
            break
        out.append(p)
    return out


# ─────────────────────────────────────────────────────────────────────────
def test_shape_validation(tmp):
    print('── ① 造型合法性：目录外一律拒收 ──')
    srv = make_server(tmp)
    before = set(srv.pets)
    for bad in (99999, 'abc', None, -1, 3.5):
        c = FakeConn()
        p = srv._add_pet(c, bad)
        check(p is None, '造型 %r 被拒' % (bad,))
        check(not c.ops(), '造型 %r 一帧都没发' % (bad,))
    check(set(srv.pets) == before, '实例表没被脏造型污染')
    check(srv._add_pet_last_error == 'bad_shape',
          '最后一次原因是 bad_shape（%s）' % srv._add_pet_last_error)
    # 合法造型仍可进（目录内 4 只轮换）
    c = FakeConn()
    p = srv._add_pet(c, 2081)
    check(p is not None and p['shape'] == 2081, '目录内造型 2081 正常入栏')
    check([op for op, _b in c.ops()] == [m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL,
                                         m.OP_SUMMON_KEY],
          '成功路径帧序 48/49/53', [op for op, _b in c.ops()])


def test_level_clamp(tmp):
    print('── ② 等级钳制 ──')
    srv = make_server(tmp)
    p1 = srv._add_pet(None, 2060, level=0)
    p2 = srv._add_pet(None, 2060, level=-5)
    p3 = srv._add_pet(None, 2060, level=99999)
    p4 = srv._add_pet(None, 2060, level='7')
    check(p1['level'] == 1 and p2['level'] == 1, '0/负等级钳到 1')
    check(p3['level'] == m.PET_LEVEL_MAX,
          '超大等级钳到 PET_LEVEL_MAX=%d（%d）' % (m.PET_LEVEL_MAX, p3['level']))
    check(p4['level'] == 7, '字符串等级 "7" 正常解析')


def test_slot_limit(tmp):
    print('── ③ 栏位上限 ──')
    srv = make_server(tmp)
    added = fill_slots(srv)
    check(len(srv.pets) == m.PET_SLOT_MAX,
          '填满 %d 只（原有 1 + 新入 %d）' % (m.PET_SLOT_MAX, len(added)),
          len(srv.pets))
    c = FakeConn()
    p = srv._add_pet(c, 2081)
    check(p is None and not c.ops(), '满栏后拒收且零帧')
    check(srv._add_pet_last_error == 'full', '原因=full（%s）'
          % srv._add_pet_last_error)
    check(len(disk_pets(srv.savepath)) == m.PET_SLOT_MAX,
          '磁盘也正好 %d 只（没把拒绝的半只写进去）' % m.PET_SLOT_MAX)


def test_pid_uniqueness(tmp):
    print('── ④ pid 唯一性 ──')
    srv = make_server(tmp)
    pids = [p['pid'] for p in fill_slots(srv)]
    check(len(set(pids)) == len(pids), 'pid 全不重复 %s' % pids)
    check(pids == sorted(pids), 'pid 单调递增')
    check(srv.pet_seq == max(srv.pets), 'pet_seq == max(pid)（%d）' % srv.pet_seq)
    check(all(srv.pets[pid]['pid'] == pid for pid in srv.pets),
          '实例表键与实例字段一致')
    # 读档后 pid 空间继续（不复用）
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_d4b'),
                      savedir=os.path.dirname(srv.savepath))
    p = srv2._add_pet(None, 2081) if len(srv2.pets) < m.PET_SLOT_MAX else None
    if p is not None:
        check(p['pid'] > max(pids), '读档后新 pid %d > 旧 max %d（不复用）'
              % (p['pid'], max(pids)))
    else:
        check(True, '读档后栏位已满（跳过追加）')


def test_load_dedup(tmp):
    print('── ⑤ 读档去重 + 造型回落 ──')
    srv = make_server(tmp, tag='d5')
    p2 = srv._add_pet(None, 2081, level=5)
    srv._persist()
    # 手写一份坏存档：目录外造型的条目
    sv = json.load(open(srv.savepath, encoding='utf-8'))
    sv['pets'].append({'pid': 999999, 'shape': 88888, 'level': 3,
                       'name': 'x', 'hp': 10, 'skills': []})
    json.dump(sv, open(srv.savepath, 'w', encoding='utf-8'), ensure_ascii=False)
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_d5b'),
                      savedir=os.path.join(tmp, 'save_d5'))
    check(999999 in srv2.pets, '非法造型的条目仍入栏（造型被修正）')
    check(srv2.pets[999999]['shape'] in m.PET_CATALOG,
          '造型已回落目录默认（%d）' % srv2.pets[999999]['shape'])
    # 重复 pid：只改**后加入的那条副本**（level=99），首条应保持 5 级
    srv3dir = os.path.join(tmp, 'save_d5c')
    shutil.copytree(os.path.join(tmp, 'save_d5'), srv3dir)
    sv = json.load(open(os.path.join(srv3dir, 'role.json'), encoding='utf-8'))
    first = [e for e in sv['pets'] if int(e['pid']) == p2['pid']][0]
    dup = dict(first)
    dup['level'] = 99                          # 副本 99 级；首条不动
    sv['pets'].append(dup)
    json.dump(sv, open(os.path.join(srv3dir, 'role.json'), 'w',
                       encoding='utf-8'), ensure_ascii=False)
    srv3 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_d5c'), savedir=srv3dir)
    q = srv3.pets.get(p2['pid'])
    check(q is not None and q['level'] == 5,
          '重复 pid 保留首条（level=%d，不是被 99 级副本覆盖）'
          % (q or {}).get('level'))
    check(len(srv3.pets) == len(set(int(e['pid']) for e in sv['pets'])),
          '去重后实例数 == 存档里去重后的 pid 数（%d）' % len(srv3.pets))


def test_sync_persist_failure(tmp):
    print('── ⑥ 原子性·同步落盘失败 → 回滚 + 零帧 ──')
    srv = make_server(tmp)
    srv._persist()                              # 先成功落一次，磁盘有基线
    disk_before = disk_pets(srv.savepath)
    real_save = m.save_role
    m.save_role = lambda *a, **k: False           # 之后落盘必然失败
    try:
        before = set(srv.pets)
        seq0 = srv.pet_seq
        c = FakeConn()
        p = srv._add_pet(c, 2081, level=5)
        check(p is None, '落盘失败 → 返回 None（不误报成功）')
        check(set(srv.pets) == before, '实例表回滚（没留半个实例）')
        check(srv.pet_seq == seq0, 'pet_seq 还原（%d）' % srv.pet_seq)
        check(not c.ops(), '一帧都没发（客户端不会收到未确认的交付）')
        check(srv._add_pet_last_error == 'persist_failed',
              '原因=persist_failed（%s）' % srv._add_pet_last_error)
        disk = disk_pets(srv.savepath)
        check(len(disk) == len(disk_before),
              '磁盘宠物数不变（%d，没把拒绝的半只写进去）' % len(disk))
    finally:
        m.save_role = real_save
    # 恢复后能正常入栏（回滚没留下后遗症）
    c = FakeConn()
    p = srv._add_pet(c, 2081)
    check(p is not None and len(c.ops()) == 3, '恢复落盘后正常交付')


def test_async_persist_failure(tmp):
    print('── ⑦ 原子性·异步落盘失败 → 回滚 + 零帧 ──')
    srv = make_server(tmp, save_async=True)
    srv._persist()
    srv.flush_saves(5.0)
    real_save = m.save_role
    m.save_role = lambda *a, **k: False
    try:
        before = set(srv.pets)
        seq0 = srv.pet_seq
        c = FakeConn()
        p = srv._add_pet(c, 2081, level=5)
        check(p is None, '异步落盘失败 → 返回 None')
        check(set(srv.pets) == before, '实例表回滚')
        check(srv.pet_seq == seq0, 'pet_seq 还原')
        check(not c.ops(), '异步路径同样零帧')
        check(srv._add_pet_last_error == 'persist_failed',
              '原因=persist_failed（%s）' % srv._add_pet_last_error)
    finally:
        m.save_role = real_save
    c = FakeConn()
    p = srv._add_pet(c, 2081)
    check(p is not None and len(c.ops()) == 3, '恢复后异步交付正常')
    srv.stop()


def test_save_blocked(tmp):
    print('── ⑧ 存档被禁（原文件损坏）→ 拒绝交付 ──')
    sub = os.path.join(tmp, 'save_blocked')
    os.makedirs(sub)
    with open(os.path.join(sub, 'role.json'), 'w', encoding='utf-8') as f:
        f.write('{ 这不是合法 JSON')                 # 损坏的原文件
    srv = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                     logdir=os.path.join(tmp, 'log_blocked'), savedir=sub)
    srv._arm_cmd_timer = lambda: None
    check(srv._save_blocked is True, '损坏存档 → _save_blocked')
    before = set(srv.pets)
    c = FakeConn()
    p = srv._add_pet(c, 2081)
    check(p is None and not c.ops(), '被禁时拒收且零帧')
    check(set(srv.pets) == before, '实例表未变')
    check(srv._add_pet_last_error == 'save_blocked',
          '原因=save_blocked（%s）' % srv._add_pet_last_error)
    with open(os.path.join(sub, 'role.json'), encoding='utf-8') as f:
        check(f.read() == '{ 这不是合法 JSON', '原文件一个字节都没被改')


def test_success_roundtrip(tmp):
    print('── ⑨ 成功路径：帧序/落盘/重登 ──')
    srv = make_server(tmp)
    c = FakeConn()
    p = srv._add_pet(c, 2060, level=7, name='小山猪')
    ops = [op for op, _b in c.ops()]
    check(ops == [m.OP_SUMMON_INFO, m.OP_SUMMON_SKILL, m.OP_SUMMON_KEY],
          '48/49/53 三帧齐序', ops)
    check(srv._add_pet_last_error is None, '错误位清零')
    d = disk_pets(srv.savepath)
    check(p['pid'] in d and d[p['pid']]['level'] == 7
          and d[p['pid']]['name'] == '小山猪',
          '磁盘含该宠（level=7 name=小山猪）')
    # 重登读回
    srv2 = m.M4Server('127.0.0.1', 0, m.SEED_DEFAULT, 0, enemies=1,
                      logdir=os.path.join(tmp, 'log_d9b'),
                      savedir=os.path.dirname(srv.savepath))
    q = srv2.pets.get(p['pid'])
    check(q is not None and q['level'] == 7 and q['name'] == '小山猪'
          and q['shape'] == 2060, '重登后逐字段一致')
    c2 = FakeConn()
    srv2._send_all_pets(c2)
    infos = [op for op, _b in c2.ops() if op == m.OP_SUMMON_INFO]
    check(len(infos) == len(srv2.pets), '重登逐只下发 48（%d 只）' % len(infos))


def main():
    tmp = tempfile.mkdtemp(prefix='m4deliver_')
    try:
        test_shape_validation(tmp)
        test_level_clamp(tmp)
        test_slot_limit(tmp)
        test_pid_uniqueness(tmp)
        test_load_dedup(tmp)
        test_sync_persist_failure(tmp)
        test_async_persist_failure(tmp)
        test_save_blocked(tmp)
        test_success_roundtrip(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = ok is not True
    print('\n=== 宠物交付闭环 %s ===' % ('FAIL' if bad else 'PASS'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
