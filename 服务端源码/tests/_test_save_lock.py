# -*- coding: utf-8 -*-
"""存档并发：账号级文件锁 + 丢更新检测。

为什么需要（2026-10-19 实测）：
  MultiServer 下同一账号可以有多个会话（重连残留、双开、GM 另开一个），
  它们各持一份内存状态，落档时都写同一个 role_<账号>.json。
  仅靠 mkstemp + os.replace 的**原子性**只保证「文件不会是半截」，
  保证不了「后写的覆盖先写的」——那是**丢失更新**。
  实测未加锁时：两个线程各写 200 次 → 29 次写失败（Windows 上并发 os.replace 冲突）。

本测试断言四件事：
  1. 并发落档不抛异常、文件始终是合法 JSON（锁保证「不写坏」）
  2. 两个会话写同一账号时，会检测到「被他人写过」并告警（不静默丢进度）
  3. 不同账号写不同文件，互不影响（锁按路径分桶，不是全局锁）
  4. 跨进程锁文件存在且可清理，锁本身不残留
"""
import json
import os
import shutil
import sys
import tempfile
import subprocess
import threading
import time

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


def new_session(savedir, account, logs=None):
    """造一个绑定到指定账号的会话，日志可选收集。"""
    srv = m.M4Server('127.0.0.1', 0, savedir=savedir,
                     logdir=tempfile.mkdtemp(prefix='lock_log_'))
    srv._bind_account(account)
    if logs is not None:
        srv.log = lambda line, _L=logs: _L.append(line)
    return srv


def main():
    tmp = tempfile.mkdtemp(prefix='lock_save_')
    try:
        # ── 1. 并发写同一账号：不写坏、不抛异常 ──
        path = os.path.join(tmp, 'role_race.json')
        errs = []

        def writer(tag):
            for i in range(120):
                try:
                    ok = m.save_role({'who': tag, 'seq': i},
                                     path)
                    if not ok:
                        errs.append('write returned False')
                except Exception as e:            # noqa: BLE001
                    errs.append(repr(e))

        ts = [threading.Thread(target=writer, args=(t,)) for t in ('A', 'B', 'C')]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=60)
        check('三线程并发写 360 次：无异常、无失败', not errs,
              ('%d 次失败 %r' % (len(errs), errs[:2])) if errs else '全部成功')
        check('并发写后文件仍是合法 JSON', isinstance(m.load_role(path), dict),
              'load_role -> %r' % (m.load_role(path) is not None,))

        # ── 2. 两个会话写同一账号：检测到「被他人写过」 ──
        tmp2 = tempfile.mkdtemp(prefix='lock_two_')
        logs = []
        s1 = new_session(tmp2, '100860001', logs)
        s2 = new_session(tmp2, '100860001', logs)
        check('两会话 savepath 相同（同账号同文件）',
              s1.savepath == s2.savepath,
              os.path.basename(s1.savepath))
        check('两会话的写者标识不同',
              s1._save_tag != s2._save_tag,
              '%s vs %s' % (s1._save_tag, s2._save_tag))

        s1.coins = 500
        s1._persist()
        check('落档写入 _save_tag',
              (m.load_role(s1.savepath) or {}).get('_save_tag') == s1._save_tag)
        logs.clear()
        s2.coins = 600
        s2_ok = s2._persist()  # P0：发现已有写者后必须拒写，不能覆盖 s1
        on_disk = m.load_role(s1.savepath) or {}
        check('另一会话覆盖被拒绝', s2_ok is False)
        check('拒写后保留原 _save_tag 与进度',
              on_disk.get('_save_tag') == s1._save_tag
              and on_disk.get('coins') == 500,
              'tag=%r coins=%r' % (on_disk.get('_save_tag'), on_disk.get('coins')))
        conflict = [ln for ln in logs if '另一会话' in ln]
        check('检测到「存档被另一会话写过」并告警', bool(conflict),
              conflict[0].strip() if conflict else '（无告警）')
        check('冲突标记 _save_conflict 置位', s2._save_conflict is True)

        # 非并发的进程重启/顺序重连必须能接管旧档；载入时看到的旧 tag 是
        # 合法基线，不能把所有重启后的首次保存都误判为冲突。
        s3 = new_session(tmp2, '100860001', logs)
        s3.coins = 700
        s3_ok = s3._persist()
        after_restart = m.load_role(s3.savepath) or {}
        check('顺序重启可接管旧档并保存',
              s3_ok is True and after_restart.get('coins') == 700,
              'ok=%r coins=%r' % (s3_ok, after_restart.get('coins')))

        # ── 3. 不同账号写不同文件，互不影响（锁按路径分桶）──
        tmp3 = tempfile.mkdtemp(prefix='lock_multi_')
        a = new_session(tmp3, 'accA')
        b = new_session(tmp3, 'accB')
        a.coins = 111
        b.coins = 222
        a._persist()
        b._persist()
        ra = m.load_role(a.savepath)
        rb = m.load_role(b.savepath)
        check('不同账号写不同文件', a.savepath != b.savepath,
              '%s / %s' % (os.path.basename(a.savepath),
                           os.path.basename(b.savepath)))
        check('不同账号互不覆盖', ra.get('coins') == 111 and rb.get('coins') == 222,
              'A=%s B=%s' % (ra.get('coins'), rb.get('coins')))
        check('不同账号不触发冲突告警', not a._save_conflict,
              'a._save_conflict=%r' % (a._save_conflict,))

        # ── 4. 锁文件行为 ──
        lk = m._CrossProcessLock(path)
        with lk:
            inside = lk.acquired
        check('跨进程锁可获取', inside)
        check('锁文件已创建', os.path.exists(path + '.lock'))
        # ★ 跨进程锁必须真排他：另起一个进程持锁，本进程应拿不到。
        # 只测 acquired=True 是自欺——那连「锁是全局变量」都能通过。
        xp = os.path.join(tmp, "role_xp.json")
        child_src = (
            "import sys, time\n"
            "sys.path.insert(0, {sim!r})\n"
            "import m4_server as m\n"
            "lk = m._CrossProcessLock({xp!r}, timeout=0.2)\n"
            "with lk:\n"
            "    print(lk.acquired, flush=True)\n"
            "    time.sleep(1.5)\n"
        ).format(sim=SIM, xp=xp)
        child_py = os.path.join(tmp, "lock_child.py")
        with open(child_py, "w", encoding="utf-8") as f:
            f.write(child_src)
        proc = subprocess.Popen([sys.executable, child_py],
                                stdout=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace",
                                cwd=SIM)
        try:
            # 子进程导入 m4_server 时会先打印 [world_config] 一行，
            # 必须跳过它再找持锁结果（否则永远读到日志行）。
            got = ''
            for _ in range(10):
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line in ('True', 'False'):
                    got = line
                    break
            check("子进程成功持锁", got == "True", "子进程最后读到 %r" % got)
            time.sleep(0.25)
            mine = m._CrossProcessLock(xp, timeout=0.2)
            with mine:
                check("子进程持锁期间，本进程拿不到同一把跨进程锁",
                      mine.acquired is False)
        finally:
            proc.wait(timeout=10)
        time.sleep(0.2)
        after = m._CrossProcessLock(xp, timeout=1.0)
        with after:
            check("子进程退出后可重新获取", after.acquired is True)

        # 同一路径的进程内锁是同一个对象（按绝对路径分桶）
        check('同一路径复用同一把进程内锁',
              m._path_lock(path) is m._path_lock(path))
        check('不同路径是不同锁',
              m._path_lock(path) is not m._path_lock(path + '.other'))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('存档锁：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  ✗ %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
