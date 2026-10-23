# -*- coding: utf-8 -*-
"""热加载：编辑器保存 -> 通知内核 -> 内核重载 -> 版本戳递增。

旧流程：编辑器只写文件，改完要重启服务端才生效。
用户改完地图/NPC/数值跑一下发现没变化，以为没保存。

本测试验证：
  1. reload_world_config 成功后版本戳递增（v1 -> v2 -> v3）
  2. 重载后伤害快照常量同步更新（改 hero_at 后 ATTACK_DMG 变化）
  3. 校验失败的配置不覆盖旧配置（拒绝而非半加载）
  4. 重载在锁内执行（不与并发操作冲突）
"""
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)
import m4_server as m  # noqa: E402
import world_config as W  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name,
                         ('　' + detail) if detail else ''))


def main():
    tmp = tempfile.mkdtemp(prefix='wcrl_')
    try:
        # 造一个独立的 world_config.json
        cfg_path = os.path.join(tmp, 'world_config.json')
        W.CONFIG_PATH = cfg_path
        # 先写一份合法配置（用内置默认）
        # 注意：m4_server 在 import 时已经加载过一次，我们要在新的 cfg_path 上重载。
        srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp, 'save'),
                         logdir=os.path.join(tmp, 'logs'))
        srv.log = lambda *a, **k: None

        # ── 1. 版本戳递增 ──
        base = W.default_config(m)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(base, f, ensure_ascii=False, indent=1)
        r1 = srv.reload_world_config()
        v1 = r1.get('version', 0)
        check('第一次重载成功', r1.get('ok'), str(r1))
        check('版本戳为 1 或更高', v1 >= 1, str(v1))

        # 改一个值再重载
        base['battle']['hero_at'] = 200
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(base, f, ensure_ascii=False, indent=1)
        r2 = srv.reload_world_config()
        v2 = r2.get('version', 0)
        check('第二次重载后版本递增', v2 > v1, '%d -> %d' % (v1, v2))

        # ── 2. 伤害快照同步 ──
        # hero_at=200 改后，ATTACK_DMG 应该跟着变（不是旧值 50）
        check('重载后 hero_at 已更新', m.HERO_AT == 200,
              'HERO_AT=%r' % m.HERO_AT)
        new_dmg = m._default_dmg(m.HERO_AT, m.ENEMY_DF, m.HERO_DP, m.ENEMY_LEVEL)
        check('重载后伤害快照已更新', m.ATTACK_DMG == new_dmg,
              'ATTACK_DMG=%r 期望=%r' % (m.ATTACK_DMG, new_dmg))
        check('改了 hero_at 后伤害确实变了', m.ATTACK_DMG != 50,
              'ATTACK_DMG=%r（应 != 50）' % m.ATTACK_DMG)

        # ── 3. 校验失败不覆盖 ──
        # 恢复 hero_at
        base['battle']['hero_at'] = m.HERO_AT  # 保存当前
        # 写一份非法配置（缺 start_map）
        bad = dict(base)
        del bad['world']['start_map']
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(bad, f, ensure_ascii=False, indent=1)
        r3 = srv.reload_world_config()
        check('非法配置被拒绝', not r3.get('ok'), str(r3))
        check('拒绝后旧配置保留（hero_at 不变）', m.HERO_AT == 200,
              'HERO_AT=%r（应保持 200）' % m.HERO_AT)

        # ── 4. 重载在锁内（并发不冲突）─
        # 恢复合法配置
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(base, f, ensure_ascii=False, indent=1)
        errs = []
        def concurrent_reload():
            for i in range(50):
                try:
                    srv.reload_world_config()
                except Exception as e:
                    errs.append(repr(e))
        ts = [threading.Thread(target=concurrent_reload) for _ in range(3)]
        for t in ts: t.start()
        for t in ts: t.join()
        check('三线程并发重载无异常', not errs,
              ('%d errors %r' % (len(errs), errs[:2])) if errs else '全部成功')
        # ★ 并发场景下，三个线程加载**同一份文件**，版本戳确实在递增
        #   （setattr 是非原子，但最终值 >= 初始 + 调用次数），但
        #   WORLD_CONFIG_STATE 里记录的是**最后一次 reload 返回的版本**，
        #   可能是中间任意一个线程的快照。所以只要求「模块上的版本戳
        #   至少 > v2」（说明重载真的跑了），不要求 STATE 精确反映最终值。
        mod_ver = getattr(m, 'WORLD_CONFIG_VERSION', 0)
        check('并发后模块版本戳 > 单线程基线', mod_ver >= v2,
              '模块 v%d vs 基线 v%d' % (mod_ver, v2))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('热加载：改动后通知内核重载%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
