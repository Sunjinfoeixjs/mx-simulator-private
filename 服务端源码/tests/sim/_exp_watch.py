# -*- coding: utf-8 -*-
"""真机实验守望器：轮询调试台 /api/frames，实时打印实验相关 opcode（含逐字段解码）。

覆盖本次实验关心的全部环节：
  C2S 96 点NPC / 98 对话选项 / 99 购买 / 106 卖出 / 100★任务交互 / 101★物品详情 /
       48 任务查询 / 54 移动 / 129 宠物资质
  S2C 16 属性初始化 / 72 任务 / 86 移动回包 / 147 商店 / 148 收购 / 151★物品详情应答 / 152 对话

用法： python _exp_watch.py [分钟数，默认 30]
"""
import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:8090'
WATCH = {
    'C2S': {96, 98, 99, 100, 101, 106, 48, 54, 129},
    'S2C': {16, 72, 86, 147, 148, 151, 152},
}
TAG = {
    ('C2S', 100): '★ 新分支① NPC 任务交互',
    ('C2S', 101): '★ 新分支② NPC 物品详情请求',
    ('S2C', 151): '★ 新应答 S2CNpcDetails（build_npc_details/op151）',
    ('S2C', 72): '任务 TaskAdd',
}


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    t0 = time.time()
    since = 0
    n = 0
    print('[守望] 开始，最长 %.0f 分钟；调试台 %s' % (minutes, BASE), flush=True)
    while time.time() - t0 < minutes * 60:
        try:
            d = json.load(urllib.request.urlopen(
                BASE + '/api/frames?since=%d' % since, timeout=5))
        except Exception:                                   # noqa: BLE001
            time.sleep(1)
            continue
        for f in d.get('frames', []):
            since = max(since, f.get('seq', since))
            op, dr = f.get('op'), f.get('dir')
            if dr == 'C2S' and op == -1:                    # 握手裸区等
                continue
            if op not in WATCH.get(dr, ()):
                continue
            n += 1
            tag = TAG.get((dr, op), '')
            print('%s %s op=%-3d %-24s %s'
                  % (f.get('ts', '--:--:--'), dr, op, f.get('name', ''), tag),
                  flush=True)
            for r in (f.get('rows') or [])[:14]:
                print('        %-14s %s' % (r.get('name', ''), r.get('disp', '')),
                      flush=True)
        time.sleep(0.8)
    print('[守望] 结束，共 %d 帧' % n, flush=True)


if __name__ == '__main__':
    main()
