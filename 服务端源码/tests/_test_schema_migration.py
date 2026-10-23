# -*- coding: utf-8 -*-
"""存档 schema 版本与迁移。

背景：存档是**用户资产**，字段随功能增加。没有版本号时，「老档缺新字段」
只能靠各读取点的 d.get(k, 默认) 兜底；一旦某字段需要**转换**（不只是补默认值），
就散落成隐式约定，改一处漏一处。

本测试锁定四件事：
  1. 版本 0（无 _schema）老档能迁移到当前版本，且 items/tasks 键转成 int
  2. 逐级迁移：0→1→2 每一步都被执行（不是跳版）
  3. 迁移后**字段完整**：所有 __init__ 会读的键都显式存在（不再依赖 d.get 兜底）
  4. 拒绝可疑档：版本比程序新、_schema 非法、迁移函数缺失 —— 一律不可读
     （上层据此 _save_blocked=True，保住用户文件而不是覆盖它）
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp(prefix='mig_')
    try:
        # ── 1. 版本 0 老档：无 _schema，items/tasks 是字符串键 ──
        old0 = {
            'px': 10, 'py': 20, 'coins': 777,
            'items': {'2001': {'shape': 22100, 'qty': 5, 'pos': 20},
                      '2002': {'shape': 12, 'qty': 10, 'pos': 21}},
            'tasks': {'1': {'state': 1, 'progress': 2}},
            'attrs': {'32': 11, '36': 12},
            'magics': {'1101': 1},
        }
        p0 = os.path.join(tmp, 'role_v0.json')
        write_json(p0, old0)

        raw = m.load_role(p0, migrate=False)
        check('migrate=False 返回原始档（带字符串键）',
              isinstance(raw, dict) and '2001' in raw['items'],
              'items keys=%r' % (list(raw['items'])[:2] if raw else None))

        d = m.load_role(p0)
        check('版本 0 老档可读', isinstance(d, dict))
        check('迁移后 _schema 为当前版本', d.get('_schema') == m.SCHEMA_VERSION,
              '_schema=%r' % (d.get('_schema'),))
        check('items 键转为 int', all(isinstance(k, int) for k in d['items']),
              'keys=%r' % (list(d['items'])[:3],))
        check('tasks 键转为 int', all(isinstance(k, int) for k in d['tasks']),
              'keys=%r' % (list(d['tasks'])[:3],))
        check('原有值不被迁移破坏',
              d['coins'] == 777 and d['px'] == 10
              and d['items'][2001]['qty'] == 5,
              'coins=%s px=%s' % (d.get('coins'), d.get('px')))

        # ── 2. 逐级迁移（不跳版）──
        d2, steps = m.migrate_role(dict(old0))
        check('逐级执行 0→1→2（不是跳版）',
              steps == ['0→1', '1→2'], 'steps=%r' % (steps,))
        # 已经是新版本的档，不应再跑迁移
        d3, steps2 = m.migrate_role(dict(d))
        check('已是最新版的档不重复迁移', steps2 == [], 'steps=%r' % (steps2,))

        # ── 3. 迁移后字段完整（不依赖 d.get 兜底）──
        need = ['hero_hp', 'hero_res', 'items', 'pet_hp', 'active_pet_id',
                'hero_shape', 'coins', 'iid_seq', 'mapid', 'tasks', 'exp',
                'next_exp', 'grade', 'points', 'attrs', 'magics', 'tili',
                'huoli', 'life_lv', 'life_exp', 'team']
        missing = [k for k in need if k not in d]
        check('迁移后关键字段全部存在', not missing, '缺 %r' % (missing,))
        check('_migrate_1_to_2 的默认值与常量同源',
              d.get('pet_hp') == m.PET_HP or 'pet_hp' in old0,
              'pet_hp=%r PET_HP=%r' % (d.get('pet_hp'), m.PET_HP))

        # ── 4. 拒绝可疑档 ──
        # 4a. 版本比程序新
        p_future = os.path.join(tmp, 'role_future.json')
        write_json(p_future, {'_schema': m.SCHEMA_VERSION + 5, 'coins': 1})
        check('版本比程序新 → 不可读（禁止覆盖）',
              m.load_role(p_future) is None)
        # 4b. _schema 非法
        p_bad = os.path.join(tmp, 'role_bad.json')
        write_json(p_bad, {'_schema': 'abc', 'coins': 1})
        check('_schema 非法 → 不可读', m.load_role(p_bad) is None)
        # 4c. 迁移函数缺失（临时挖掉一级）
        saved = dict(m.MIGRATIONS)
        try:
            m.MIGRATIONS.pop(1, None)
            p_v1 = os.path.join(tmp, 'role_v1.json')
            write_json(p_v1, {'_schema': 1, 'coins': 1})
            check('缺迁移函数 → 不可读（不半迁移）', m.load_role(p_v1) is None)
        finally:
            m.MIGRATIONS.clear()
            m.MIGRATIONS.update(saved)

        # ── 4d. 「版本对、内容坏」：迁移不触发，但条目仍须被规范化 ──
        #   2026-10-19 踩坑：手写档/外部工具改过的档可能标着 _schema=2，
        #   却带 key="0"（字符串）。迁移按版本跳过 → 字符串原样进 build_item_add
        #   → struct.error → 进世界时 socket 线程崩溃 → 客户端卡住。
        #   所以条目规范化必须**独立于版本**，每次读档都跑。
        p_mixed = os.path.join(tmp, 'role_mixed.json')
        write_json(p_mixed, {
            '_schema': m.SCHEMA_VERSION,          # 版本已是最新
            'coins': 42,
            'items': {'2001': {'shape': 22100, 'key': '0', 'qty': '5', 'pos': 20}},
        })
        dm = m.load_role(p_mixed)
        check('版本已最新但条目键是字符串 → 仍被规范化',
              isinstance(dm.get('items'), dict)
              and all(isinstance(k, int) for k in dm['items'])
              and dm['items'][2001]['key'] == 0
              and dm['items'][2001]['qty'] == 5,
              'items=%r' % (dm.get('items'),))
        check('normalize_items 补齐缺失字段',
              m.normalize_items({'2001': {'shape': 12}})[2001]
              == {'shape': 12, 'key': 0, 'qty': 1, 'pos': 20, 'infoext': ''})
        check('normalize_items 丢弃坏条目（无 shape / 非数字）',
              m.normalize_items({'1': {'key': 1}, '2': {'shape': 12, 'key': 'abc'}}) == {})

        # ── 4e. 因果链验证：不规范化会真的崩（不只是「值不对」）──
        #   4d 断言「值被规范化」，本条断言「不规范化会导致 struct.pack 崩溃」。
        #   两条合起来才完整：前者证明有修复，后者证明**修复是必要的**。
        import struct as _st
        bad_items = {'2001': {'shape': 22100, 'key': '0', 'qty': '5', 'pos': 20}}
        _raw = json.loads(json.dumps(bad_items))     # 模拟从文件读回的原始 dict
        try:
            _st.pack('<H', _raw['2001']['key'])
            _crashed = False
        except (_st.error, TypeError):
            _crashed = True
        check('未经规范化的字符串 key 会让 struct.pack 崩（证明规范化必要）',
              _crashed,
              'key=%r type=%s' % (_raw['2001']['key'],
                                  type(_raw['2001']['key']).__name__))
        # 规范化后必须能 pack
        _norm = m.normalize_items(bad_items)
        try:
            _st.pack('<H', _norm[2001]['key'])
            _ok_pack = True
        except (_st.error, TypeError):
            _ok_pack = False
        check('规范化后 key 可安全 struct.pack', _ok_pack,
              'key=%r type=%s' % (_norm[2001]['key'],
                                  type(_norm[2001]['key']).__name__))

        # ── 5. 落盘带 _schema ──
        d4 = m.M4Server('127.0.0.1', 0, savedir=tmp,
                        logdir=tempfile.mkdtemp(prefix='mig_log_'))
        d4._bind_account('migacc')
        d4.coins = 12345
        d4._persist()
        on_disk = m.load_role(d4.savepath, migrate=False)
        check('落盘写入 _schema',
              on_disk.get('_schema') == m.SCHEMA_VERSION,
              '_schema=%r' % (on_disk.get('_schema'),))
        check('落盘后可直接读回（无需迁移）',
              m.load_role(d4.savepath).get('coins') == 12345)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('存档 schema 迁移：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  ✗ %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
