# -*- coding: utf-8 -*-
"""扫描源码生成 §17 符号索引（markdown 片段）。

为什么要有这个脚本：
  文档里的符号清单原先手写维护，代码一改就漏——补一个 build_xxx 忘了加进表，
  删一个方法表里还留着。行号版本更糟（漂移 900+ 行），所以 2026-10-20 起改成
  「只写符号名 + 脚本生成」：清单永远和代码一致，人手不用碰。

扫描什么：
  · def build_*            S2C 组帧函数（服务端 → 客户端的线序定义）
  · class *                顶层类
  · 大写常量赋值            只取「有语义的」，跳过纯数据表
  · 关键方法（白名单）        M4Server 的分派/战斗/存档等主干

用法：
    python _gen_anchor_table.py            # 打印 markdown 片段
    python _gen_anchor_table.py --write    # 就地替换文档 §17 的代码块
    python _gen_anchor_table.py --check    # 只校验，不一致返回 1（挂 CI）
"""
from __future__ import print_function

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
SIM = os.path.join(PROJECT_ROOT, '服务端源码', 'tests', 'sim')
DOC = os.path.join(HERE, '模拟服全链路代码解析.md')

BEGIN = '<!-- BEGIN: 符号索引（脚本生成，勿手改） -->'
END = '<!-- END: 符号索引 -->'

# 生成时跳过的常量：纯数据表（几十上百项），列进索引只会淹没真正有用的符号
SKIP_CONST_PREFIX = (
    'MAP_', 'NAME2MAP', 'MAPS', 'MOCK_', 'TEAM_DUMMY', 'ITEM_ALL',
    'HERO_MAGIC', 'PET_', 'ENEMY_SKILL', 'DEFAULT_', 'C2S_', 'S2C_',
    'OP_', 'TASK_', 'NPC_', 'SHOP_', 'DROP_', 'GEN_',
)
# 但 opcode 段标记要单独保留（它们是导航入口）
KEEP_CONST = set(['DISPATCH_PRE_WAR_OPS', 'C2S_SINGLEPLAYER_IGNORED',
                  'WORLD_CONFIG_STATE', 'ATTACK_DMG', 'ENEMY_ATTACK_DMG',
                  'PET_DAMAGE', 'DMG_MODEL', 'DMG_MODEL_CAVEAT',
                  'SCHEMA_VERSION', 'MIGRATIONS', 'SEED_DEFAULT',
                  'SERVER_NUM'])

# M4Server 里值得列进索引的方法（主干链路，不是全部 131 个）
KEY_METHODS = [
    'reload_world_config', '_log_dmg_banner', '_enter_world', '_start_war',
    '_persist', '_bind_account', '_hero_level_up', '_hero_add_points',
    '_hero_learn_magic', '_award_battle_exp', '_handle_npc_buy',
    '_handle_npc_sell', '_handle_npc_make', '_turn_in_task', '_on_enemy_dead',
    '_speed_order', '_finish_pet_turn', '_hero_attack', '_roll_drops',
    '_after_hero_action', '_enemy_attack', '_resolve_hero_round',
    '_fallback_turn', '_send_scene', '_goto_map', '_check_exit',
    '_resync_scene', 'serve_background', 'run', 'stop', '_safe_stop',
    'handle', '_dispatch', 'log',
]


def read(path):
    return io.open(path, encoding='utf-8', errors='replace').read()


def scan_module(path):
    """扫一个源文件，返回 (builds, classes, consts, methods)。"""
    src = read(path)
    lines = src.split(chr(10))
    builds, classes, consts, methods = [], [], [], []
    for l in lines:
        if l.startswith('def build_'):
            builds.append(l[4:].split('(')[0])
        elif l.startswith('class '):
            cn = l[6:].split('(')[0].split(':')[0].strip()
            if not cn.startswith('_'):
                classes.append(cn)
        elif re.match(r'^[A-Z][A-Z0-9_]*\s*=', l):
            cn = l.split('=')[0].strip()
            if cn in KEEP_CONST or not cn.startswith(SKIP_CONST_PREFIX):
                consts.append(cn)
        elif l.startswith('    def ') and not l.startswith('    def __'):
            mn = l.strip()[4:].split('(')[0]
            if mn in KEY_METHODS:
                methods.append(mn)
    return builds, classes, consts, methods


def gen():
    """生成 markdown 片段（不含外层 fence）。"""
    out = []
    m4 = os.path.join(SIM, 'm4_server.py')
    builds, classes, consts, methods = scan_module(m4)
    out.append('【内核 m4_server.py】')
    out.append('  常量 / 状态')
    for c in consts:
        out.append('    %s' % c)
    out.append('  顶层类')
    for c in classes:
        out.append('    %s' % c)
    out.append('  S2C 组帧（%d 个）' % len(builds))
    for b in builds:
        out.append('    %s' % b)
    out.append('  关键方法')
    for m in methods:
        out.append('    %s' % m)

    extra = [
        ('tap.py', '拦截层'),
        ('debugger.py', '调试台'),
        ('editor_server.py', '世界编辑器'),
        ('world_config.py', '世界配置'),
    ]
    for fn, title in extra:
        p = os.path.join(SIM, fn)
        if not os.path.isfile(p):
            continue
        names = []
        for l in read(p).split(chr(10)):
            if l.startswith('def ') or l.startswith('class '):
                nm = l.split('(')[0].split(':')[0].strip()
                nm = nm[4:] if nm.startswith('def ') else nm[6:]
                if not nm.startswith('_'):
                    names.append(nm)
        if names:
            out.append('')
            out.append('【%s %s】' % (title, fn))
            for nm in names:
                out.append('  %s' % nm)
    return chr(10).join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description='生成 §17 符号索引')
    ap.add_argument('--write', action='store_true', help='就地更新文档')
    ap.add_argument('--check', action='store_true', help='只校验（不一致返回 1）')
    args = ap.parse_args(argv)

    body = gen()
    if args.write:
        doc = read(DOC)
        if BEGIN not in doc or END not in doc:
            print('文档缺少生成标记')
            return 2
        i = doc.index(BEGIN) + len(BEGIN)
        j = doc.index(END)
        fence = chr(96) * 3
        new = doc[:i] + chr(10) + fence + chr(10) + body + chr(10) + fence + chr(10) + doc[j:]
        io.open(DOC, 'w', encoding='utf-8').write(new)
        print('已更新 §17（%d 行）' % len(body.split(chr(10))))
        return 0
    if args.check:
        doc = read(DOC)
        if BEGIN not in doc or END not in doc:
            print('文档缺少生成标记')
            return 2
        i = doc.index(BEGIN) + len(BEGIN)
        j = doc.index(END)
        fence = chr(96) * 3
        want = chr(10) + fence + chr(10) + body + chr(10) + fence + chr(10)
        if doc[i:j].strip() != want.strip():
            print('§17 与源码不一致——跑 python _gen_anchor_table.py --write')
            return 1
        print('§17 与源码一致（%d 行）' % len(body.split(chr(10))))
        return 0
    print(body)
    return 0


if __name__ == '__main__':
    sys.exit(main())