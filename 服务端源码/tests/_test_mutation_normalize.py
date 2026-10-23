# -*- coding: utf-8 -*-
"""变异测试：`load_role` 的**每次读档规范化**必须被守护。

为什么单独做这个：
  `load_role` 里有两条规范化调用（items / tasks），它们**独立于 schema 版本**
  每次读档都跑。这两行很容易被后人当成「迁移的一部分」删掉或挪进 MIGRATIONS——
  那样版本对但内容坏的档就会带着字符串 key 进去：
    · items 带字符串 key → build_item_add 里 struct.pack 崩，进世界时 socket 线程异常
    · tasks 带字符串 key → `tasks.get(int_tid)` 永远查不到，任务栏静默消失
      （2026-10-20 真实踩过：`_migrate_0_to_1` 里做过 int 还原，但 schema=2 的档
       不跑迁移，于是重启后任务与进度全部"丢失"，且不报错）

本脚本做**变异测试**：把规范化那行替换掉，跑对应守护测试，必须判红。
判红 = 测试确实守住了这行；没判红 = 这行没人管，随时可能被误删。

用法：
    python _test_mutation_normalize.py        # 跑全部变异，期望都判红
    python _test_mutation_normalize.py -v     # 打印变异版输出

退出码：所有变异都判红 → 0（守护有效）；有任一变异没判红 → 1（守护失效）。
"""
import io
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')

# 变异用例：(名字, 原行, 替换行, 守护测试模块, 调用方式)
#   'main'   —— 模块提供 main()，返回退出码
#   'import' —— 脚本式模块（模块级执行 + 末尾 sys.exit），import 即跑
# ★ 替换成「直通原始值」而不是 pass：pass 会让后面的赋值抛 NameError，
#   那样变异版是**崩了**而不是**断言失败**，判红的原因就不可信了。
CASES = [
    (
        'items 每次读档规范化',
        "        norm_items = normalize_items(raw_items)",
        "        norm_items = raw_items  # [变异] 去掉 items 规范化",
        '_test_schema_migration', 'main',
    ),
    (
        'tasks 每次读档规范化',
        "        norm_tasks = normalize_tasks(raw_tasks)",
        "        norm_tasks = raw_tasks  # [变异] 去掉 tasks 规范化",
        '_test_task', 'import',
    ),
]


def run_one(name, needle, replace, guardian, mode, src, verbose):
    """对单条用例做变异，返回 (判红成功?, 说明)。"""
    if needle not in src:
        return False, '变异目标不存在：%r（若已重构该行，请同步更新 CASES）' % needle

    mut = src.replace(needle, replace, 1)
    if mut == src:
        return False, '替换未生效'

    tmp = tempfile.mkdtemp(prefix='mut_norm_')
    io.open(os.path.join(tmp, 'm4_server.py'), 'w', encoding='utf-8').write(mut)

    tail = ('sys.exit(T.main())' if mode == 'main'
            else 'import T  # noqa: F401  脚本式：import 即跑，末尾自己 sys.exit')
    prog = chr(10).join([
        'import sys, importlib.util',
        'sys.dont_write_bytecode = True',
        'sys.path.insert(0, r"%s")' % SIM,
        'import m4_server as _o',
        'spec = importlib.util.spec_from_file_location('
        '    "m4_server", r"%s")' % os.path.join(tmp, 'm4_server.py'),
        'm = importlib.util.module_from_spec(spec)',
        'sys.modules["m4_server"] = m',
        'spec.loader.exec_module(m)',
        'assert m.__file__.startswith(r"%s"), m.__file__' % tmp,
        'sys.path.insert(0, r"%s")' % HERE,
        'import %s as T' % guardian,
        tail,
    ])
    runner = os.path.join(tmp, 'run_mut.py')
    io.open(runner, 'w', encoding='utf-8').write(prog)

    try:
        r = subprocess.run([sys.executable, runner], capture_output=True,
                           text=True, encoding='utf-8', errors='replace',
                           timeout=180)
    except subprocess.TimeoutExpired:
        return False, '变异版超时（可能是死循环，需人看）'

    out = (r.stdout or '') + (r.stderr or '')
    # 「失败项」= 测试自己打的失败行（[FAIL] / FAIL ... 两种风格都认）
    fails = [ln.strip() for ln in out.split(chr(10))
             if ln.strip().startswith('[FAIL]') or ln.strip().startswith('FAIL')]
    if verbose:
        print('--- %s 变异版输出 ---' % name)
        print(out)
        print('--- end ---')

    # ★ 崩溃不算「被判红」：Traceback 说明变异把代码改坏了（NameError/AttributeError…），
    #   而不是被断言抓住。那种情况下守护其实没生效，必须报出来。
    if 'Traceback (most recent call last)' in out:
        return False, '变异版崩了（Traceback），说明是改坏而非被断言抓住'

    if r.returncode != 0 and fails:
        return True, '变异被判红（%d 项失败），首个：%s' % (len(fails), fails[0][:100])
    return False, 'rc=%r，失败项=%d —— 这行无人守护' % (r.returncode, len(fails))


def main(argv=None):
    verbose = '-v' in (argv or sys.argv[1:])
    src_path = os.path.join(SIM, 'm4_server.py')
    src = io.open(src_path, encoding='utf-8').read()

    bad = 0
    for (name, needle, replace, guardian, mode) in CASES:
        ok, msg = run_one(name, needle, replace, guardian, mode, src, verbose)
        print(('[PASS] ' if ok else '[FAIL] ') + name)
        print('       %s' % msg)
        if not ok:
            bad += 1

    if bad:
        print('%d / %d 条变异未被判红 —— 对应规范化行缺守护' % (bad, len(CASES)))
        if not verbose:
            print('       加 -v 看变异版完整输出')
        return 1
    print('全部 %d 条变异都被判红 → load_role 的每次读档规范化**有测试守护**'
          % len(CASES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
