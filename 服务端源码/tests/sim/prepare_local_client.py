# -*- coding: utf-8 -*-
"""本地接管准备：生成 local.ini（第一条服务器→127.0.0.1）+ 修改 mx.ini（serverini=local.ini）。

动作（幂等、可一键还原）：
  1. 读 client/server.ini（官方列表，三层格式）→ 解码
  2. 第一条服务器行改为「本地测试服 127.0.0.1 3134 …」（其余字段保留）
  3. 写出 client/local.ini（**明文 GBK**，server.ini 原样不动）
     ★ 必须是明文：客户端 `C_sys.RecordString` 是恒等函数，只读明文；
       写成 serverini_encode 的三层加密格式会弹「服务器列表需要更新」
       （2026-10-20 修正：本脚本此前一直写加密格式，与 singleplayer.rewrite_local_ini
       的真机实证相悖——一周真机会话用的都是明文版，加密版会触发更新模态框）
  4. 备份 mx.ini → mx.ini.bak_before_local（仅首次）
  5. mx.ini [Game] 写入：
        serverini=local.ini               ← 选服界面改读本文件
        refreshserver=4102444800.0        ← 远期时间戳：跳过启动自动刷新
                                             （否则会拉官方列表并覆盖显示）
  6. 创建 client/nocheck 空标记（官方自带的「测试版」逃生舱，见下）

关于 nocheck（2026-10-20 残留校验排查）：
  客户端 `main.py` 启动时 `g_SpecialVersion = os.path.exists('nocheck')`，
  为真时**整体跳过 `blacklist.StartCheck()`**——那里面挂着几条对单机模拟服
  有害的本地校验：
    · `C_sys.IsVPC()` 为真 → `ExitWindows()`：在虚拟机里跑客户端会**直接关机**；
    · `CheckFps()`：渲染帧率偏离内置上限 → 上报 + 60 tick 后断线(009)；
    · `CheckMX()`：≥4 个客户端窗口 → 退出（与 MultiServer 多开测试冲突）；
    · `CheckBadUrl/CheckBadProcess`：依赖服务端下发的黑名单（本服从不发，
      本就空转，但 IsVPC/FPS/多开三条不依赖黑名单）。
  这是**客户端自带的官方开关**（内部测试版用），不是二进制补丁；删掉文件即还原。
  代价仅三点：启动弹一次「测试版本」提示、GM 对话框变为可用、NPC 对话与聊天
  窗口显示 chatid 调试信息。
还原:  python prepare_local_client.py --revert
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serverini_encode import decode_file  # noqa: E402

CLIENT = r'D:\mxsj2013\client'
LOCAL_NAME = 'local.ini'
BAK_NAME = 'mx.ini.bak_before_local'
LOCAL_IP = '127.0.0.1'
LOCAL_PORT = 3134
FAR_FUTURE = '4102444800.000000'   # 2100-01-01，抑制启动自动刷新
LOCAL_SERVER_LABEL = '本地测试服'
NOCHECK_NAME = 'nocheck'           # 官方测试版开关：存在即跳过 blacklist.StartCheck


def make_local_text(text):
    lines = text.split('\r\n')
    for i, ln in enumerate(lines):
        f = ln.split()
        if not f or f[0] == 'ip' or len(f) < 4:
            continue                      # 空行/分隔/代理行/区名行
        old = lines[i]
        f[0], f[1] = LOCAL_SERVER_LABEL, LOCAL_IP
        if len(f) > 2 and f[2].isdigit():
            f[2] = str(LOCAL_PORT)
        lines[i] = ' '.join(f)
        return '\r\n'.join(lines), old, lines[i]
    raise RuntimeError('未在列表中定位到服务器行')


def update_mx_ini(path):
    raw = open(path, 'rb').read()
    nl = '\r\n' if b'\r\n' in raw else '\n'
    text = raw.decode('gbk')
    lines = text.replace('\r\n', '\n').split('\n')
    # 移除旧的受管键（全文件范围，仅这两个键由本脚本管理）
    kept = [ln for ln in lines
            if not ln.strip().lower().startswith(('serverini=', 'refreshserver='))]
    # 定位 [Game]
    gi = None
    for i, ln in enumerate(kept):
        if ln.strip().lower() == '[game]':
            gi = i
            break
    new_block = ['[Game]', 'serverini=%s' % LOCAL_NAME, 'refreshserver=%s' % FAR_FUTURE]
    if gi is None:
        kept = new_block + kept
    else:
        kept = kept[:gi] + new_block + kept[gi + 1:]
    out = nl.join(kept)
    if raw.endswith(b'\r\n') and not out.endswith(nl):
        out += nl
    return raw.decode('gbk'), out


def prepare(client_dir):
    ini = os.path.join(client_dir, 'server.ini')
    mx = os.path.join(client_dir, 'mx.ini')
    local = os.path.join(client_dir, LOCAL_NAME)
    assert os.path.isfile(ini), '缺少 server.ini: %s' % ini
    assert os.path.isfile(mx), '缺少 mx.ini: %s' % mx

    # 1-3) 生成 local.ini（明文 GBK + CRLF，与 rewrite_local_ini 维护的格式一致）
    flag, plain, text = decode_file(ini)
    new_text, old_line, new_line = make_local_text(text)
    open(local, 'wb').write(new_text.encode('gbk'))
    # 验证：按客户端的方式（明文 GBK 直读）回读
    back = open(local, 'rb').read().decode('gbk')
    assert new_line in back, 'local.ini 回读校验失败'
    print('[1] 已生成 %s' % local)
    print('    改前: %s' % old_line)
    print('    改后: %s' % new_line)

    # 4) 备份 mx.ini（仅首次）
    bak = os.path.join(client_dir, BAK_NAME)
    if not os.path.isfile(bak):
        shutil.copy2(mx, bak)
        print('[2] 已备份 mx.ini -> %s' % bak)
    else:
        print('[2] 备份已存在（保留首次备份）: %s' % bak)

    # 5) 更新 mx.ini
    before, after = update_mx_ini(mx)
    open(mx, 'w', encoding='gbk', newline='').write(after)
    print('[3] mx.ini 已更新:')
    print('    --- 修改前 ---')
    print('\n'.join('    ' + l for l in before.splitlines()))
    print('    --- 修改后 ---')
    print('\n'.join('    ' + l for l in after.splitlines()))
    # 6) nocheck 标记（官方逃生舱：跳过 blacklist 本地校验套件）
    mark = os.path.join(client_dir, NOCHECK_NAME)
    if os.path.exists(mark):
        print('[4] %s 已存在（跳过反校验已生效）' % mark)
    else:
        open(mark, 'wb').close()
        print('[4] 已创建 %s（空标记：IsVPC关机/帧率009/多开退出等本地校验全部跳过）' % mark)

    print()
    print('完成。启动模拟服后，选服界面第一行「%s」即指向 %s:%d。' %
          (LOCAL_SERVER_LABEL, LOCAL_IP, LOCAL_PORT))
    print('还原: python prepare_local_client.py --revert')


def revert(client_dir):
    mx = os.path.join(client_dir, 'mx.ini')
    bak = os.path.join(client_dir, BAK_NAME)
    local = os.path.join(client_dir, LOCAL_NAME)
    mark = os.path.join(client_dir, NOCHECK_NAME)
    assert os.path.isfile(bak), '未找到备份 %s' % bak
    shutil.copy2(bak, mx)
    if os.path.isfile(local):
        os.remove(local)
    if os.path.isfile(mark):
        os.remove(mark)
        print('已删除 %s（恢复 blacklist 本地校验）' % mark)
    print('已还原 mx.ini（自 %s），并删除 %s' % (bak, local))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    client_dir = args[0] if args else CLIENT
    if '--revert' in sys.argv:
        revert(client_dir)
    else:
        prepare(client_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
