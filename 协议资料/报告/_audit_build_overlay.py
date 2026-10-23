# -*- coding: utf-8 -*-
"""build_* ↔ OVERLAY 一致性审计（服务端线序对账）。

为什么需要它：`tap.py` 的 `OVERLAY/_OV_S2C` 是「解码覆盖表」，其 docstring 明说
「逐字段对齐 m4_server.build_*」。但 build_* 改了而 OVERLAY 忘了改，调试台就会把字段
读错位——而且这种漂移不会报错，只在可视化里悄悄串字段。本脚本把每个 build_* 真正跑一遍
（哑参数），剥掉帧头，用 OVERLAY 反解，验证「服务端发出的字节 == 解码器展示的字段」。

这是 _extract_wire.py（客户端 Unpack ↔ OVERLAY）的**服务端镜像**：三角一致性里
build_* / OVERLAY / 客户端 Unpack 三者必须互相一致。

用法： python _audit_build_overlay.py
"""
from __future__ import print_function
import inspect
import re
import struct
import sys

SIM = r'C:\Users\Administrator\MX\服务端源码\sim'
sys.path.insert(0, SIM)

import m4_server as m
import tap


def strip_body(frame):
    """剥掉 make_s2c 的帧头，返回真正的字段体（不含 opcode）。

    make_s2c: payload = [opcode] + data; frame = [N][opcode][data]（N==0xFF 时 0xFF+u16）。
    """
    if frame[0] == 0xFF:
        return frame[4:]
    return frame[2:]


def dummy_arg(p):
    """按签名给哑参数：有默认用默认；否则按名字/类型猜一个能跑通的值。

    列表型参数（entries/items/list/members/skills）返回占位 None，由 try_build 展开成
    多种形状重试——因为 build_* 的循环体可能是「int 列表」(skills) 或
    「3/4/5 元组列表」(npc 收购/学技清单)，猜死一种形状会误报 CALL_FAIL。
    """
    if p.default is not inspect.Parameter.empty:
        return p.default
    n = p.name.lower()
    if n in ('x', 'y'):
        return 0
    # desc/ext/text 同样是「变长 C 串」实参（build_make_complete/npc_details/item_info…），
    # 猜 0 会让 var_gbk(int) 抛 AttributeError，被判成 CALL_FAIL（工具局限，不是真漂移）。
    if any(k in n for k in ('name', 'title', 'msg', 'npc', 'info',
                            'desc', 'ext', 'text')):
        return 'x'
    if n == 'base':
        return {}                      # build_pet_info 里 base['xxx'] 当字典用
    if any(k in n for k in ('entries', 'items', 'list', 'members', 'skills')):
        return None                    # 占位，try_build 展开
    if any(k in n for k in ('ability', 'resist')):
        return (0, 0, 0, 0, 0)        # 抗性/资质常以定长元组出现
    return 0


def _base_args(sig):
    """每个参数一个基础值；列表型参数留 None 占位。"""
    return [dummy_arg(p) for p in sig.parameters.values()]


# 列表型参数的多种候选形状：先试「单 int 列表」(skills=技能id 列表)，
# 再试 3/4/5 元组（npc 清单各行 arity 不同）。覆盖绝大多数 build_* 循环体。
_LIST_SHAPES = (0, (0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0, 0))


def try_build(fn, sig):
    """用哑参数真正调用 build_*，列表型参数自动试多种形状，返回 (frame, err)。"""
    base = _base_args(sig)
    list_idx = [i for i, a in enumerate(base) if a is None]
    if not list_idx:
        try:
            return fn(*base), None
        except Exception as e:                       # noqa: BLE001
            return None, e
    last_err = None
    for shp in _LIST_SHAPES:
        cand = list(base)
        for i in list_idx:
            cand[i] = [shp]                           # 单行列表：[int] 或 [(...)]
        try:
            return fn(*cand), None
        except Exception as e:                       # noqa: BLE001
            last_err = e
    return None, last_err


# ── 显式哑参覆盖：启发式猜不出的形状（多类型元组 / 需特定 key 的字典 / 动态 opcode）
# 一旦登记，审计就完全自包含、可重复；新的 build_* 漂移能被自动抓到。
MANUAL_DUMMY = {
    'build_add_npc':       (0, 0, 0, 0, 'x'),                 # (npc_id, shape, x, y, name)
    'build_family_members': ([(0, 'x', 0, 0, 0)],),           # members: [(mid,name,grade,cls,online)]
    'build_goto_m':        (0, 0),                            # (x, y)
    'build_minimap_npc':   (0, [(0, 0, 'x', '')]),            # (minimap, [(x,y,name,ext)])
    'build_pet_info':      (0, 'x', 1, None),                 # base 占位，下方替换为 pet_base_dict()
    'build_pet_prop_view': (0, 0, (0, 0, 0, 0, 0), (0,) * 9), # (pid, extra_zz, ability×5, resist×9)
    'build_pet_skill':     (0, [0], None),                    # (pid, [skill_id], hf_from)
    # op54 召唤兽装备：equips 是循环行（{pos: (shape, desc)} / {pos: {'shape','desc'}} /
    # [(pos, shape, desc)]）。启发式给 0 不可迭代 → 显式登记；pos 必须非 0，
    # 否则会被当成哨兵、循环直接结束（同 147/148/149 的坑）。
    'build_pet_equip':     (0, {1: (0, 'x')}),                 # (pid, {pos: (shape, desc)})
    'build_scene_m':       (1, 'x', 1, 0, 0),                 # (mapid, name, minimap, x, y)
    'build_server_time':   (m.OP_TIME,),                      # 默认走 dlgcalendar.S2CTime(192)
    'build_task_add':      (0, 'x', '', 1),                   # (tid, name, desc, tasktype)
    'build_npc_details':   (0, 'x'),                          # (pos, desc) desc=变长 C 串
    # op17 通用版：哑参必须给「idx 与 width 一致」的合法组合——真实调用方都一致
    # （m_MP=18 是 TWORD 2B），否则 _OV_DYN[17] 按 ITEM_ALL[idx] 解出的宽度对不上，
    # 会报假 MISMATCH（曾踩：idx=0 m_Icon 1B + 默认 width=2 → 3B 帧只吃进 2B）。
    'build_prop_change':   (m.PROP_IDX_MP, 0, 2),
    # op105 随行宠物：entries 每行是**4 元组** (style, shape, name, title)，
    # 而 _LIST_SHAPES 只试 3/5 元组 → 必然 unpack 失败。
    # 即使补上 4 元组形状，name 取 0（int 没有 .encode）仍会 CALL_FAIL，
    # 所以这里给**显式**哑参：名字/称号必须是 str。
    'build_map_summon':    (1, [(0, 4005, 'x', '')]),
    # ★ 2026-10-20：147/148/149 的 OVERLAY 从 T 改成 L{}（循环构造），
    #   哑参必须用非零 shape/skid，否则 shape=0 会被当成哨兵、循环直接结束。
    'build_npc_buylist':    (7, [(50010, 1, 100)]),
    'build_npc_sell_list':  ([(50010, 5, 50)],),
    'build_npc_learn_list': ([(1104, 0, 255, 0)],),
}


# build_server_time 的 opcode 是形参（两通道 192/15），静态抓不到常量，显式登记。
OP_OVERRIDE = {'build_server_time': m.OP_TIME}                # 192


def _resolve_dummy(name, fn):
    """返回 (args, err)：优先用 MANUAL_DUMMY，否则启发式 try_build。"""
    if name in MANUAL_DUMMY:
        args = list(MANUAL_DUMMY[name])
        if name == 'build_pet_info':
            args[3] = m.pet_base_dict()                       # base 必须是带 key 的字典
        try:
            return fn(*args), None
        except Exception as e:                                # noqa: BLE001
            return None, e
    try:
        sig = inspect.signature(fn)
        return try_build(fn, sig)
    except Exception as e:                                    # noqa: BLE001
        return None, e


def opcode_of(fn):
    """从源码里抓 make_s2c(NAME, ...) 的 opcode 常量名 → 数值。"""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return None, None
    mm = re.search(r'make_s2c\(\s*(\w+)\s*,', src)
    if not mm:
        return None, None
    name = mm.group(1)
    return name, getattr(m, name, None)


def main():
    overlay = tap.OVERLAY['S2C']
    dyn = getattr(tap, '_OV_DYN', {})
    builds = [(n, getattr(m, n)) for n in dir(m)
              if n.startswith('build_') and callable(getattr(m, n))]

    ok = mismatch = no_ov = has_dyn = call_fail = 0
    rows = []

    for name, fn in sorted(builds, key=lambda kv: kv[0]):
        opname, op = opcode_of(fn)
        if name in OP_OVERRIDE:                      # 动态 opcode 显式登记（如 build_server_time）
            op = OP_OVERRIDE[name]
            opname = 'OP_OVERRIDE'
        if op is None:
            rows.append((name, opname, 'NO_OPCODE', '', ''))
            call_fail += 1
            continue
        # 构造哑参数：优先 MANUAL_DUMMY，否则启发式 try_build（列表型自动试多形状）
        try:
            frame, err = _resolve_dummy(name, fn)
        except Exception as e:                        # noqa: BLE001
            rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL',
                         '', repr(e)[:80]))
            call_fail += 1
            continue
        if err is not None:
            rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL',
                         '', repr(err)[:80]))
            call_fail += 1
            continue
        if not isinstance(frame, (bytes, bytearray)):
            rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL', '',
                         '返回非字节: %s' % type(frame)))
            call_fail += 1
            continue
        body = strip_body(bytes(frame))
        # 动态布局（_OV_DYN）：静态覆盖表只覆盖前缀——用动态处理器真解一遍再下结论
        if op in dyn:
            try:
                layout, dyn_note = dyn[op](body)
            except Exception as e:                       # noqa: BLE001
                rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL',
                             'body=%dB' % len(body), 'dyn 处理器异常: %r' % e[:60]))
                call_fail += 1
                continue
            if layout is None:
                # 宽度确实不能静态确定（如 type 越界）——如实标 HAS_DYN，不算通过也不算失败
                rows.append((name, '%s=%s' % (opname, op), 'HAS_DYN',
                             'body=%dB' % len(body), dyn_note or '布局随内容变'))
                has_dyn += 1
                continue
            try:
                _rows, tail, note, consumed = tap.decode_fields(layout, body)
            except Exception as e:                       # noqa: BLE001
                rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL',
                             'body=%dB' % len(body), 'dyn 解码异常: %r' % e[:60]))
                call_fail += 1
                continue
            if (not tail) and consumed == len(body) and (not note):
                ok += 1
                rows.append((name, '%s=%s' % (opname, op), 'OK(dyn)',
                             'body=%dB 吃满' % len(body),
                             ('经 _OV_DYN 动态布局验证' + ('；' + dyn_note if dyn_note else ''))))
            else:
                mismatch += 1
                det = 'tail=%sB' % len(tail) if tail else ''
                if note:
                    det += (' ' if det else '') + 'note=%s' % note
                if consumed != len(body):
                    det += ' consumed=%d/%d' % (consumed, len(body))
                rows.append((name, '%s=%s' % (opname, op), 'MISMATCH',
                             'body=%dB' % len(body), det))
            continue
        if op not in overlay:
            if op in dyn:
                rows.append((name, '%s=%s' % (opname, op), 'HAS_DYN',
                             'body=%dB' % len(body), '静态前缀外还有动态布局'))
                has_dyn += 1
            else:
                rows.append((name, '%s=%s' % (opname, op), 'NO_OVERLAY',
                             'body=%dB' % len(body), 'OVERLAY 无此 opcode'))
                no_ov += 1
            continue
        # 用 OVERLAY 反解
        try:
            fields = overlay[op]
            res = tap.decode_fields(fields, body)
            if len(res) == 4:
                _rows, tail, note, consumed = res
            else:
                _rows, tail, note, consumed = res + (None, None, None)
        except Exception as e:
            rows.append((name, '%s=%s' % (opname, op), 'CALL_FAIL',
                         'body=%dB' % len(body), 'decode 异常: %s' % repr(e)[:60]))
            call_fail += 1
            continue
        clean = (not tail) and (consumed == len(body)) and (not note)
        if clean:
            ok += 1
            rows.append((name, '%s=%s' % (opname, op), 'OK',
                         'body=%dB 吃满' % len(body), ''))
        else:
            mismatch += 1
            det = 'tail=%sB' % len(tail) if tail else ''
            if note:
                det += (' ' if det else '') + 'note=%s' % note
            if consumed != len(body):
                det += ' consumed=%d/%d' % (consumed, len(body))
            rows.append((name, '%s=%s' % (opname, op), 'MISMATCH',
                         'body=%dB' % len(body), det))

    # 输出
    print('%-26s %-14s %-10s %-14s %s' % ('build_*', 'opcode', '结论', '体量', '细节'))
    print('-' * 100)
    for name, opc, st, sz, det in rows:
        print('%-26s %-14s %-10s %-14s %s' % (name, opc, st, sz, det))
    print()
    print('build_* ↔ OVERLAY 一致性：OK %d / MISMATCH %d / NO_OVERLAY %d / HAS_DYN %d / CALL_FAIL %d'
          % (ok, mismatch, no_ov, has_dyn, call_fail))
    # ★ 退出码口径（2026-10-19 收紧）：
    #   原先只认 MISMATCH，于是 NO_OVERLAY / CALL_FAIL 全都被当成「通过」——
    #   op129/op105 这两个**每场战斗 / 每次进图都会发**的帧长期没有覆盖表条目，
    #   回归却一直是绿的。缺条目 = 调试台看不到字段（只有 hex），与线序漂移同属
    #   「三角一致性缺一环」，必须一起判红。
    #   HAS_DYN 仍是「合法但宽度无法静态确定」，不算失败。
    bad = mismatch + no_ov + call_fail
    if bad:
        print()
        print('✗ 存在必须修的项：MISMATCH %d / NO_OVERLAY %d / CALL_FAIL %d'
              % (mismatch, no_ov, call_fail))
        if no_ov:
            print('  NO_OVERLAY = 该 op 在 tap.OVERLAY 里没有条目 → 调试台只有 hex，无字段视图')
        if call_fail:
            print('  CALL_FAIL  = 哑参跑不通或反解异常 → 先确认是「工具局限」还是真漂移')
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
