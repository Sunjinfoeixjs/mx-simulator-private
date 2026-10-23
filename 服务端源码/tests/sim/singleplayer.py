# -*- coding: utf-8 -*-
"""单机游戏架构 · 宿主（唯一进程入口）
====================================

把原来的「两个进程、两套生命周期」——

    客户端 mx.exe ──TCP 127.0.0.1:3134──▶ m4_server.py（独立进程）

——改成「一个进程管全部」的单机游戏形态：

    ┌─ singleplayer.py（唯一进程，一条命令）──────────────────────────┐
    │                                                                │
    │   GameKernel   m4_server.M4Server（守护线程）                    │
    │                 · 登录/选角/进世界/移动/NPC/回合制战斗/物品/宠物   │
    │                 · 状态即会话（单角色单连接）                      │
    │                 · 存档 role.json（可注入目录）                    │
    │   Transport    LoopbackListener 127.0.0.1:0（自动空闲端口）       │
    │   HttpHijack   本地 HTTP 劫持 127.0.0.1:8080                     │
    │                 · 客户端 update.ini 的 ServerList / MiniPage /   │
    │                   DownPage / GuidePage 全指向这里                 │
    │                 · 不回它 → 「服务器列表需要刷新」+「脚本错误」弹窗  │
    │   ClientCfg    local.ini（明文 GBK，端口跟随实际监听端口）         │
    │                 + mx.ini（serverini= / refreshserver=）          │
    │                 + 前置校验（update.ini 版本 / 补丁 DLL）          │
    │   ClientHost   mx.exe 子进程托管；客户端退出 → 落盘 → 宿主退出     │
    └────────────────────────────────────────────────────────────────┘

相对旧架构的实质变化：
  1. **端口不再硬编码**：内核 `port=0` 让操作系统挑空闲端口，客户端配置按
     真实端口生成 → 消灭「3134 被占用 → 客户端连不上」这类单机故障。
  2. **生命周期合一**：先起内核再拉客户端；客户端退出宿主跟着退，不会留下
     一个没人连的空转服务端；Ctrl-C 先收客户端再落存档。
  3. **单连接显式化**：同一时刻只允许一个客户端，第二个直接拒绝并关闭，
     保护 `_conn / in_war / enemy_hp` 这一套单例状态不被覆盖。
  4. **GM 面板只绑回环**：原来是 `0.0.0.0:8080`，会暴露到局域网。
  5. **存档目录可注入**：`--save-dir` 可把单机进度放到任意位置，
     与 sim 开发调试用的存档分开。

用法：
    python singleplayer.py                     # 一键单机启动（默认客户端目录见 DEFAULT_CLIENT_DIR）
    python singleplayer.py --client <mx.exe>   # 指定客户端可执行文件
    python singleplayer.py --no-client         # 只起内核（脚本联调 / 自测）
    python singleplayer.py --fresh --no-encounter --web
    python singleplayer.py --port 3134         # 固定端口（默认 0 = 自动）
    python singleplayer.py --http-port 8081    # 换 HTTP 劫持端口

还原客户端配置：`python prepare_local_client.py --revert`（mx.ini 备份还原），
或直接删掉客户端目录下的 `local.ini` 并把 mx.ini 的 `serverini=` 行去掉。
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m4_server as m  # noqa: E402

DEFAULT_CLIENT_DIR = r'D:\mxsj2013\client'
CLIENT_EXE = 'mx.exe'        # 游戏主程序（内嵌 CPython 客户端；清单 requireAdministrator，
                             #   由 launch_client 以 ShellExecuteEx(runas) 提权启动）
# ★ 2026-10-16 真机复核定案：`gameplayer.exe` 实为 .mwr **录像播放器**
#   （窗口标题「录像播放器」、内嵌串「是否退出播放器」、从不连模拟服），
#   不是游戏进程，**不再作为客户端入口**。
LOCAL_INI = 'local.ini'
MX_INI = 'mx.ini'
UPDATE_INI = 'update.ini'
PATCHED_DLL = 'mxmain.dll'
HTTP_PORT = 8080          # 客户端 update.ini 里 ServerList/MiniPage 指向的端口

# update.ini 需要伪装的版本号：官方补丁链已断（version.txt 静态冻结在 1.2.73），
# 版本号对不上客户端会反复走更新流程，进不了选服界面。
REQUIRED_VERSION = '1.2.73'
# 未打补丁（加密+压缩仍开启）的 mxmain.dll md5。
# 加密模式（默认）期望命中此 md5；命中补丁版（ea2f3c46…）则加密链路不成立。
ORIG_DLL_MD5 = 'b50406bc0c1b73287d35aab0cca93017'
# 2100-01-01：远期时间戳，跳过启动自动刷新（否则会用官方列表覆盖本地列表）
FAR_FUTURE = '4102444800.000000'
LOCAL_SERVER_LABEL = '本地测试服'


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def rewrite_local_ini(client_dir, port):
    """把客户端目录下的 local.ini 指向 `127.0.0.1:<port>`（明文 GBK，原地改写）。

    ★ 必须是明文：客户端 `C_sys.RecordString` 是恒等函数，只读明文；
      写成 serverini_encode 的三层加密格式会弹「服务器列表需要更新」。
    ★ 只改已存在的「本地测试服」行的端口号，其余行原样保留——
      官方列表结构（区名行 / 分隔线）不动，避免破坏选服界面解析。
    返回 (是否成功, 说明)。
    """
    path = os.path.join(client_dir, LOCAL_INI)
    if not os.path.isfile(path):
        return False, ('未找到 %s。请先按《本地测试操作指南》配置一次客户端，'
                       '或用 --client 指定已配置好的目录。' % path)
    raw = open(path, 'rb').read()
    nl = '\r\n' if b'\r\n' in raw else '\n'
    try:
        text = raw.decode('gbk')
    except UnicodeDecodeError:
        return False, '%s 不是 GBK 文本（可能是加密格式），请改用明文列表。' % path
    lines = text.replace('\r\n', '\n').split('\n')
    hit = 0
    for i, ln in enumerate(lines):
        f = ln.split()
        if len(f) >= 4 and f[1] == '127.0.0.1' and f[2].isdigit():
            f[2] = str(port)
            lines[i] = ' '.join(f)
            hit += 1
    if not hit:
        return False, ('%s 里没有指向 127.0.0.1 的服务器行；请先按《本地测试操作指南》'
                       '把第一行改成「%s 127.0.0.1 <端口> 3 ·推荐」（恰好 5 字段）。'
                       % (path, LOCAL_SERVER_LABEL))
    out = nl.join(lines)
    if raw.endswith(b'\r\n') and not out.endswith(nl):
        out += nl
    # 先备份再写（仅首次）
    bak = path + '.bak_singleplayer'
    if not os.path.isfile(bak):
        shutil.copy2(path, bak)
    open(path, 'wb').write(out.encode('gbk'))
    return True, '%s 已指向 127.0.0.1:%d（改写 %d 行，备份 %s）' % (
        LOCAL_INI, port, hit, os.path.basename(bak))


def update_mx_ini(client_dir):
    """在 mx.ini 的 [Game] 写入 serverini=local.ini 与远期 refreshserver。

    与 prepare_local_client.update_mx_ini 的差别：只动这两个受管键，
    [Game] 下其它键原样保留（旧实现会把整个 [Game] 替换成 3 行）。
    """
    path = os.path.join(client_dir, MX_INI)
    if not os.path.isfile(path):
        return False, '未找到 %s' % path
    raw = open(path, 'rb').read()
    nl = '\r\n' if b'\r\n' in raw else '\n'
    text = raw.decode('gbk')
    lines = text.replace('\r\n', '\n').split('\n')
    out, in_game, seen_game, inserted = [], False, False, False
    for ln in lines:
        s = ln.strip().lower()
        if s.startswith('[') and s.endswith(']'):
            in_game = (s == '[game]')
            if in_game:
                seen_game = True
            out.append(ln)
            if in_game:
                out.append('serverini=%s' % LOCAL_INI)
                out.append('refreshserver=%s' % FAR_FUTURE)
                inserted = True
            continue
        if in_game and s.startswith(('serverini=', 'refreshserver=')):
            continue                      # 受管键已在上方写入，去掉重复项
        out.append(ln)
    if not seen_game:
        out += ['[Game]', 'serverini=%s' % LOCAL_INI,
                'refreshserver=%s' % FAR_FUTURE]
        inserted = True
    if not inserted:
        return False, '未能在 mx.ini 中定位 [Game] 段'
    res = nl.join(out)
    if raw.endswith(b'\r\n') and not res.endswith(nl):
        res += nl
    bak = path + '.bak_singleplayer'
    if not os.path.isfile(bak):
        shutil.copy2(path, bak)
    open(path, 'wb').write(res.encode('gbk'))
    return True, '%s 已设置 serverini=%s / refreshserver=%s（备份 %s）' % (
        MX_INI, LOCAL_INI, FAR_FUTURE, os.path.basename(bak))


def rebuild_server7_bin(port):
    """把本地 HTTP 劫持用的 server7_local.bin 里的「本地测试服」端口改成实际端口。

    必要性：客户端 update.ini 的 ServerList 指向 http://127.0.0.1:8080/server7.txt，
    一旦触发列表刷新就会拉这个文件；里面的端口若还是写死的 3134，刷新出来的
    列表会指向一个没人监听的端口。改成本次内核的真实端口，刷新也落得对地方。
    返回 (是否成功, 说明)。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'server7_local.bin')
    if not os.path.isfile(path):
        return False, '缺少 %s（本地列表编码件）' % path
    try:
        from serverini_encode import decode_file, encode
        flag, _plain, text = decode_file(path)
        lines, hit = text.split('\r\n'), 0
        for i, ln in enumerate(lines):
            f = ln.split()
            if len(f) >= 4 and f[0] == LOCAL_SERVER_LABEL \
                    and f[1] == '127.0.0.1' and f[2].isdigit():
                f[2] = str(port)
                lines[i] = ' '.join(f)
                hit += 1
        if not hit:
            return False, 'server7_local.bin 里没有本地测试服行'
        open(path, 'wb').write(encode('\r\n'.join(lines), flag))
        return True, 'server7_local.bin 已指向 127.0.0.1:%d（%d 行）' % (port, hit)
    except Exception as e:  # noqa: BLE001
        return False, '重编 server7_local.bin 失败：%r' % e


def start_local_http(port=HTTP_PORT):
    """在守护线程里启动本地 HTTP 劫持（复用 local_http.Handler）。

    客户端 update.ini 把这些 URL 都指向 127.0.0.1:8080：
      ServerList=.../server7.txt   列表刷新
      MiniPage / DownPage / GuidePage=.../blank   启动器内嵌浏览器
    不起这个服务 → 弹「服务器列表需要刷新」+「脚本错误」模态框，
    而「脚本错误」模态框对 SendKeys/WM_CLOSE 均不响应，是启动流程唯一的
    人工干预点（见 local_http.py:45-51 注释）。
    返回 (httpd 或 None, 说明)。
    """
    try:
        import http.server
        import local_http
    except ImportError as e:
        return None, '无法导入 local_http：%r' % e
    try:
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port),
                                                local_http.Handler)
    except OSError as e:
        return None, 'HTTP 劫持端口 %d 被占用：%r（客户端刷新列表会失败，' \
                     '可用 --http-port 换端口）' % (port, e)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name='local-http').start()
    return httpd, '本地 HTTP 劫持已启动 127.0.0.1:%d' % port


def check_prereqs(client_dir, fix_update=False, encrypt=True):
    """客户端前置校验：update.ini 版本 + mxmain.dll 原版/补丁状态。只返回告警列表。"""
    warns = []

    upd = os.path.join(client_dir, UPDATE_INI)
    if not os.path.isfile(upd):
        warns.append('缺少 %s（版本伪装文件）。官方补丁链已断，版本号对不上会'
                     '反复走更新流程；请按《本地测试操作指南》把 Version 设成 %s。'
                     % (UPDATE_INI, REQUIRED_VERSION))
    else:
        txt = open(upd, 'rb').read().decode('gbk', 'replace')
        ver = None
        for ln in txt.splitlines():
            if ln.strip().lower().startswith('version='):
                ver = ln.split('=', 1)[1].strip()
                break
        if ver != REQUIRED_VERSION:
            if fix_update:
                try:
                    lines = txt.splitlines()
                    done = False
                    for i, ln in enumerate(lines):
                        if ln.strip().lower().startswith('version='):
                            lines[i] = 'Version=%s' % REQUIRED_VERSION
                            done = True
                            break
                    if not done:
                        lines.append('Version=%s' % REQUIRED_VERSION)
                    open(upd, 'wb').write(
                        ('\r\n'.join(lines) + '\r\n').encode('gbk'))
                    warns.append('%s 的 Version 已从 %r 修为 %s（--fix-update）'
                                 % (UPDATE_INI, ver, REQUIRED_VERSION))
                except OSError as e:
                    warns.append('尝试修正 %s 失败：%r' % (UPDATE_INI, e))
            else:
                warns.append('%s 的 Version=%r，期望 %s。官方补丁链已断，'
                             '版本不符会卡在启动器更新流程；'
                             '确认要用 --fix-update 自动修正。'
                             % (UPDATE_INI, ver, REQUIRED_VERSION))

    dll = os.path.join(client_dir, PATCHED_DLL)
    if not os.path.isfile(dll):
        warns.append('缺少 %s（加密/压缩开关就在这个 DLL 里）。' % PATCHED_DLL)
    else:
        try:
            cur = _md5(dll)
        except OSError as e:
            warns.append('读取 %s 失败：%r' % (PATCHED_DLL, e))
        else:
            if encrypt and cur != ORIG_DLL_MD5:
                warns.append('%s 是**补丁版**（md5 %s…）：加密+压缩被关掉，'
                             '而服务端已启用加密——客户端将发明文、且解不开'
                             '服务端压缩帧。请还原原版 mxmain.dll（md5 %s）。'
                             % (PATCHED_DLL, cur[:8], ORIG_DLL_MD5))
            elif not encrypt and cur == ORIG_DLL_MD5:
                warns.append('%s 是**未打补丁**的原件（md5 %s…）：加密+压缩仍开启，'
                             '模拟服只发明文，客户端将解不开帧。请换成补丁件 '
                             'mxmain_nocrypto.dll（+0x72 一字节补丁，见'
                             '《单机运行距离评估》路线 A）。' % (PATCHED_DLL, cur[:8]))
    return warns


def resolve_client_exe(client_dir, exe=None):
    """决定要拉起哪个可执行文件：显式指定 > mx.exe（游戏主程序）。

    ★ `gameplayer.exe` 是 .mwr 录像播放器（非游戏进程），绝不应作为客户端入口；
      游戏入口是 `mx.exe`（清单 requireAdministrator，需提权启动）。
    """
    if exe:
        return exe
    return os.path.join(client_dir, CLIENT_EXE)


def _launch_elevated(path):
    """以管理员身份启动 path（ShellExecuteEx runas，与双击 mx.exe 的 UAC 体验一致）。

    返回一个最小 Popen 兼容壳（wait/poll/terminate），宿主可继续托管其生命周期。
    """
    import ctypes
    from ctypes import wintypes

    SEE_MASK_NOCLOSEPROCESS = 0x40
    SW_SHOWNORMAL = 1
    STILL_ACTIVE = 259

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
                    ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                    ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
                    ('hProcess', wintypes.HANDLE)]

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = 'runas'
    sei.lpFile = path
    sei.lpParameters = None
    sei.lpDirectory = os.path.dirname(os.path.abspath(path))  # ★ mx.exe 启动即校验
    #   「无法获取游戏信息,请在游戏目录运行」——ShellExecuteEx 必须带游戏目录做工作目录
    sei.nShow = SW_SHOWNORMAL
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError('ShellExecuteEx(runas) 失败（WinError %d）：%s' % (err, path))
    h = sei.hProcess

    class _ElevatedProc:
        """最小 Popen 兼容壳：包装提权进程句柄。"""

        def __init__(self, handle):
            self._h = handle
            self.returncode = None

        def poll(self):
            code = wintypes.DWORD()
            if ctypes.windll.kernel32.GetExitCodeProcess(self._h, ctypes.byref(code)):
                if code.value != STILL_ACTIVE:
                    self.returncode = code.value
                    ctypes.windll.kernel32.CloseHandle(self._h)
            return self.returncode

        def wait(self, timeout=None):
            t0 = time.time()
            while self.poll() is None:
                if timeout is not None and time.time() - t0 >= timeout:
                    break
                time.sleep(0.5)
            return self.returncode

        def terminate(self):
            try:
                ctypes.windll.kernel32.TerminateProcess(self._h, 1)
            except Exception:  # noqa: BLE001
                pass

    print('[客户端] 未提权会话 → 请求 UAC 提权启动 %s（请在弹出的提示中点「是」）'
          % os.path.basename(path))
    return _ElevatedProc(h)


def launch_client(client_dir, exe=None):
    """拉起客户端进程，返回 Popen 兼容对象。

    mx.exe 清单声明 requireAdministrator：当前进程未提权时 Popen 抛 WinError 740，
    自动改用 ShellExecuteEx(runas) 提权启动（弹 UAC 确认框）。

    ★ 2026-10-20 实测（无人值守环境）：
      当前会话 IsInRole(Administrator) = False，_launch_elevated 弹出 UAC 后
      **无人确认**，客户端进程根本没起来，宿主日志只看到：
        [客户端] 未提权会话 → 请求 UAC 提权启动 mx.exe（请在弹出的提示中点「是」）
        [退出] 客户端已结束（rc=0）
      这不是 bug——是真机测试的硬前提：**必须人工点 UAC**。

      协议层的问题可以不等真机：tests/_test_client_decode.py 用客户端解码语义
      离线对照服务端帧（见文档 §12.2.2）。真机只用于验渲染/时序/本地状态机。
    """
    path = resolve_client_exe(client_dir, exe)
    if not os.path.isfile(path):
        raise FileNotFoundError('找不到客户端可执行文件：%s' % path)
    try:
        return subprocess.Popen([path], cwd=os.path.dirname(path) or '.')
    except OSError as e:
        if getattr(e, 'winerror', None) != 740:
            raise
        return _launch_elevated(path)


def _shutdown_http(httpd):
    """关闭本地 HTTP 劫持（幂等，忽略一切异常）。"""
    if httpd is None:
        return
    try:
        httpd.shutdown()
    except Exception:  # noqa: BLE001
        pass


def build_parser():
    ap = argparse.ArgumentParser(
        description='单机游戏架构宿主：游戏内核 + 客户端托管，一个进程全包')
    ap.add_argument('--client', default=None,
                    help='客户端目录（含 mx.exe / mx.ini），默认 %s'
                         % DEFAULT_CLIENT_DIR)
    ap.add_argument('--client-exe', default=None,
                    help='直接指定 mx.exe 路径（优先于 --client）')
    ap.add_argument('--no-client', action='store_true',
                    help='只起游戏内核，不拉客户端（脚本联调 / 自测用）')
    ap.add_argument('--port', type=int, default=0,
                    help='内核监听端口；0 = 自动挑选空闲端口（推荐，默认 0）')
    ap.add_argument('--host', default='127.0.0.1',
                    help='内核监听地址（默认仅回环）')
    ap.add_argument('--save-dir', default=None,
                    help='单机存档目录（默认沿用 %s）' % m.SAVE_DIR)
    ap.add_argument('--fresh', action='store_true',
                    help='删除已有存档，从出生点零进度开始')
    ap.add_argument('--encounter-every', type=int, default=5,
                    help='每移动几次触发一次遇怪战斗（默认 5）')
    ap.add_argument('--no-encounter', action='store_true',
                    help='关闭遇怪（纯探索）')
    ap.add_argument('--enemies', type=int, default=m.ENEMY_COUNT_DEFAULT,
                    help='每场战斗敌方单位数')
    ap.add_argument('--turn-timeout', type=int, default=m.TURN_TIMEOUT,
                    help='回合指令超时秒数（超时默认普攻）')
    ap.add_argument('--seed', default=None, help='握手种子（默认 0x12345678）')
    ap.add_argument('--web', action='store_true',
                    help='启用 GM 调试面板（只绑 127.0.0.1，默认关）')
    ap.add_argument('--web-port', type=int, default=8080)
    ap.add_argument('--http-port', type=int, default=HTTP_PORT,
                    help='本地 HTTP 劫持端口（客户端 update.ini 指向的端口，'
                         '默认 %d）' % HTTP_PORT)
    ap.add_argument('--no-http', action='store_true',
                    help='不启动本地 HTTP 劫持（仅在客户端已另行运行时使用）')
    ap.add_argument('--encrypt', dest='encrypt', action='store_true',
                    default=True,
                    help='启用加密+压缩（默认，配合原版 mxmain.dll）')
    ap.add_argument('--no-encrypt', dest='encrypt', action='store_false',
                    help='禁用加密（仅在仍使用补丁 mxmain_nocrypto.dll 时）')
    ap.add_argument('--fix-update', action='store_true',
                    help='自动把 update.ini 的 Version 修正为 %s' % REQUIRED_VERSION)
    return ap


def main(argv=None, on_ready=None, **over):
    """单机宿主主流程。可由 m4_server.py --single 转调，也可直接命令行跑。

    `on_ready(srv, port, client_dir)` 是纯新增的挂载点：内核与 HTTP 劫持就绪、
    客户端**尚未拉起**时回调一次。调试台（debugger.py）用它把抓包挂在
    `M4Server.handle` 上——必须早于客户端接入，否则第一条连接抓不到。
    """
    ap = build_parser()
    args = ap.parse_args(argv)
    for k, v in over.items():
        if v is not None and hasattr(args, k):
            setattr(args, k, v)

    client_dir = args.client or DEFAULT_CLIENT_DIR
    if args.client_exe:
        client_dir = os.path.dirname(os.path.abspath(args.client_exe))
    save_dir = args.save_dir or m.SAVE_DIR
    save_path = os.path.join(save_dir, 'role.json')
    seed = int(args.seed, 0) if args.seed else m.SEED_DEFAULT
    encounter_every = 0 if args.no_encounter else args.encounter_every

    if args.fresh and os.path.exists(save_path):
        try:
            os.remove(save_path)
            print('[fresh] 已删除旧存档 %s' % save_path)
        except OSError as e:
            print('[fresh] 删除存档失败（忽略）：%r' % e)

    print('=' * 66)
    print(' 《梦想世界2013》本地单机 · 单进程架构')
    print(' 游戏内核 + 回环传输 + 客户端托管')
    print('=' * 66)

    # ① 游戏内核（守护线程；port=0 → 自动空闲端口）
    srv = m.M4Server(args.host, args.port, seed, encounter_every,
                     savedir=save_dir, enemies=args.enemies,
                     turn_timeout=args.turn_timeout, encrypt=args.encrypt)
    port = srv.serve_background(args.port, web=args.web,
                                web_port=args.web_port)
    print('[内核] 已就绪 127.0.0.1:%d（seed=0x%08X，遇怪每 %d 步，回合超时 %ds）'
          % (port, seed, encounter_every, srv.turn_timeout))
    print('[存档] %s%s' % (save_path,
                           '（已有进度）' if os.path.exists(save_path) else '（新档）'))
    if args.web:
        print('[GM  ] http://127.0.0.1:%d/ （仅回环）' % args.web_port)

    # ①½ 本地 HTTP 劫持：客户端 update.ini 的 ServerList / 内嵌页面都指向这里
    httpd = None
    if not args.no_http:
        ok, msg = rebuild_server7_bin(port)
        print('[HTTP] %s' % msg)
        httpd, msg = start_local_http(args.http_port)
        print('[HTTP] %s' % msg)
        if httpd is None:
            print('[HTTP] 继续启动，但客户端刷新列表可能弹「服务器列表需要更新」。')

    # ①¾ 外部挂载点（可视化调试台等）：必须早于客户端接入，否则第一条连接抓不到
    if on_ready is not None:
        try:
            on_ready(srv, port, client_dir)
        except Exception as e:                    # noqa: BLE001
            print('[挂载] on_ready 回调失败（忽略，继续启动）：%r' % e)

    if args.no_client:
        # 无客户端模式：内核常驻，等脚本客户端来连；Ctrl-C 退出并落档
        print('[自测] 未拉客户端。可用脚本连 127.0.0.1:%d，Ctrl-C 结束。' % port)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print('\n[退出] 收到 Ctrl-C，正在落档…')
        finally:
            srv._persist()
            _shutdown_http(httpd)
        return 0

    # ② 客户端配置（端口跟随实际监听端口）
    ok, msg = rewrite_local_ini(client_dir, port)
    print('[配置] %s' % msg)
    if not ok:
        print('[配置] local.ini 未能指向本地，客户端仍可能连官方服；'
              '中止启动以免误连外网。')
        srv._persist()
        _shutdown_http(httpd)
        return 2
    ok, msg = update_mx_ini(client_dir)
    print('[配置] %s' % msg)
    for w in check_prereqs(client_dir, fix_update=args.fix_update,
                           encrypt=args.encrypt):
        print('[告警] %s' % w)

    # ③ 拉起客户端并托管
    exe_path = resolve_client_exe(client_dir, args.client_exe)
    print('[客户端] 启动 %s …' % exe_path)
    print('[提示] 选服界面第一行「%s」即本地单机服；帐号纯数字、密码 6 位。'
          % LOCAL_SERVER_LABEL)
    try:
        proc = launch_client(client_dir, args.client_exe)
    except (FileNotFoundError, RuntimeError) as e:
        print('[客户端] %s' % e)
        srv._persist()
        _shutdown_http(httpd)
        return 2

    rc = 0
    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        print('\n[退出] 收到 Ctrl-C，先收客户端再落档…')
        try:
            proc.terminate()
            rc = proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
                rc = proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
    finally:
        srv._persist()
        _shutdown_http(httpd)
        print('[退出] 客户端已结束（rc=%s），存档已写入 %s' % (rc, save_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
