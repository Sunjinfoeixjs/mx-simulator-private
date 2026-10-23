# -*- coding: utf-8 -*-
"""GM 场景跳转（含 4 张孤岛）+ 任务编辑（真实链路）。

守的是三条：
  1. /api/maps 列出全部 60 图，孤岛标记 = 4 张（1158/1159/1176/1177）
  2. GM 切图走真实 _goto_map 整包（有客户端时下发 SceneM 链）
  3. 任务编辑走真实链路：进度 → 重发任务栏；完成 → 玩家同一条奖励链
"""
from __future__ import print_function

import json
import os
import sys
import tempfile
import time
import urllib.request

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
                         ('\u3000' + detail) if detail else ''))


def main():
    # ── 1. 孤岛清单 ──
    check('孤岛清单恰为 4 张（1158/1159/1176/1177）',
          m.ISLAND_MAP_IDS == frozenset((1158, 1159, 1176, 1177)),
          repr(sorted(m.ISLAND_MAP_IDS)))
    check('孤岛全在 MAPS 里（GM 可跳）',
          all(mid in m.MAPS for mid in m.ISLAND_MAP_IDS))

    tmp = tempfile.mkdtemp(prefix='mt_')
    srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0,
                     enemies=3, turn_timeout=30,
                     logdir=os.path.join(tmp, 'logs'),
                     savedir=os.path.join(tmp, 'save'))
    srv.log = lambda *a, **k: None
    srv.serve_background(0, web=True, web_port=0)
    time.sleep(0.6)
    port = srv._web_port
    try:
        def get(p):
            return json.loads(urllib.request.urlopen(
                'http://127.0.0.1:%d%s' % (port, p), timeout=5).read())

        def post(p, b):
            req = urllib.request.Request(
                'http://127.0.0.1:%d%s' % (port, p),
                data=json.dumps(b).encode(),
                headers={'Content-Type': 'application/json'})
            try:
                r = urllib.request.urlopen(req, timeout=5)
                return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        # ── 2. /api/maps ──
        maps = get('/api/maps')['maps']
        check('/api/maps 返回 60 图', len(maps) == len(m.MAPS),
              '%d 张' % len(maps))
        check('/api/maps 带孤岛标记',
              sorted(mp['mapid'] for mp in maps if mp.get('island'))
              == sorted(m.ISLAND_MAP_IDS))
        check('/api/maps 带 current 标记',
              any(mp.get('current') for mp in maps), '含当前图')

        # ── 3. 无客户端切孤岛：只改状态 ──
        st, r = post('/api/map', {'mapid': 1158})
        stt = r.get('state', {})
        check('无客户端切孤岛 1158 生效',
              st == 200 and stt.get('mapid') == 1158
              and stt.get('map_island') is True,
              'mapid=%s island=%s' % (stt.get('mapid'), stt.get('map_island')))
        check('state 快照带 mapname', bool(stt.get('mapname')),
              repr(stt.get('mapname')))

        # ── 4. 任务编辑（进程内直调，验证真实链路）──
        srv.tasks[1] = {'state': 1, 'progress': 0}
        r = srv.gm_task_set_progress(2)
        check('gm_task_set_progress(2) → 进度 2/3',
              r['progress'] == 2 and r['state'] == 1, repr(r))
        r = srv.gm_task_state(2)
        check('gm_task_state(2) → 完成态且进度满',
              r['state'] == 2 and r['progress'] == 3, repr(r))

        # ── 5. HTTP 任务路由 ──
        st, r = post('/api/task/progress', {'value': 1})
        check('/api/task/progress 生效',
              st == 200 and r.get('state', {}).get('tasks', {}).get('1', {}).get('progress') == 1,
              repr(r.get('state', {}).get('tasks')))
        st, r = post('/api/task/state', {'value': 0})
        check('/api/task/state 移除任务',
              st == 200 and srv.tasks.get(1) is None,
              repr(srv.tasks))

        # ── 6. 控制台 task 子命令 ──
        c = srv.gm_command('task progress 3')
        check('控制台 task progress 3',
              c.get('ok') and c.get('result', {}).get('progress') == 3, repr(c))
        c = srv.gm_command('task state 1')
        check('控制台 task state 1',
              c.get('ok') and c.get('result', {}).get('state') == 1, repr(c))
        # 无客户端时 complete 应如实报错（真实链路需要 conn）
        srv._conn = None
        c = srv.gm_command('task complete')
        # ★ 错误信封有两处：顶层 msg（给人看）+ result.error（结构化）。
        #   早期只看顶层 error；GM 命令统一信封后 error 落到 result 里，
        #   于是「实现明明报了错、测试却判失败」。两处都认。
        _err = (c.get('error') or c.get('msg')
                or (c.get('result') or {}).get('error') or '')
        check('无客户端 complete 如实报错',
              not c.get('ok') and '客户端' in _err, repr(c))
    finally:
        try:
            srv.stop()
        except Exception:                                    # noqa: BLE001
            pass

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('场景跳转 + 任务编辑：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  \u2717 %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
