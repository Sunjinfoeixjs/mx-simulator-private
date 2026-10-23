# -*- coding: utf-8 -*-
"""真机启动前的端口一致性检查：local.ini 与 server7_local.bin 必须同端口。

背景（2026-10-20 真机踩坑）：
  客户端有**两个**地方记录本地服端口：
    · local.ini            —— 客户端直连用（选服后立即用，**优先**）
    · server7_local.bin    —— HTTP 劫持返回的列表（点「刷新列表」才拉）
  手工起内核时只更新了后者，界面列表是新的，但点进去连的还是 local.ini 里的
  旧端口 → 「正在连接服务器中....」→「连接服务器失败」。

  抓包证据：TCP 127.0.0.1:18484 127.0.0.1:15829 SYN_SENT
  而内核实际在 3134 监听，3134 上没有任何连接。

本测试锁定：两个文件里的本地服端口必须一致；不一致时给出可执行的修法。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, 'sim')          # sim 与 tests 同层（本工作区布局）
if not os.path.isdir(SIM):             # 兼容旧的「tests 与 sim 平级」布局
    SIM = os.path.join(os.path.dirname(HERE), 'sim')
sys.path.insert(0, SIM)

results = []


def check(name, ok, detail=''):
    d = ('　' + detail) if isinstance(detail, str) else \
        ('　' + repr(detail)) if detail else ''
    results.append((name, ok))
    print('[%s] %s%s' % ('PASS' if ok else 'FAIL', name, d))


def local_ini_ports(client_dir):
    """从 local.ini 里抽出「本地测试服」行的端口。"""
    p = os.path.join(client_dir, 'local.ini')
    if not os.path.isfile(p):
        return None
    s = io.open(p, 'rb').read().decode('gbk', 'replace')
    out = []
    for ln in s.split(chr(10)):
        f = ln.split()
        if len(f) >= 3 and f[0] == '本地测试服' and f[1] == '127.0.0.1':
            try:
                out.append(int(f[2]))
            except ValueError:
                pass
    return out


def server7_ports():
    """从 server7_local.bin 解出「本地测试服」行的端口。"""
    from serverini_encode import decode_file
    p = os.path.join(SIM, 'server7_local.bin')
    if not os.path.isfile(p):
        return None
    _flag, _plain, text = decode_file(p)
    out = []
    for ln in text.split('\r\n'):
        f = ln.split()
        if len(f) >= 3 and f[0] == '本地测试服' and f[1] == '127.0.0.1':
            try:
                out.append(int(f[2]))
            except ValueError:
                pass
    return out


def main():
    import singleplayer as sp
    client_dir = sp.DEFAULT_CLIENT_DIR

    ini = local_ini_ports(client_dir)
    s7 = server7_ports()

    check('local.ini 存在且有本地测试服行', bool(ini),
          'ports=%r' % (ini,))
    check('server7_local.bin 存在且有本地测试服行', bool(s7),
          'ports=%r' % (s7,))

    if ini and s7:
        check('两处端口**一致**（否则客户端会连旧端口）',
              set(ini) == set(s7),
              'local.ini=%r  server7=%r  修法：sp.rewrite_local_ini(dir, %d)'
              % (ini, s7, s7[0] if s7 else 0))

        # 端口必须在合法范围（防止把 0 或越界值写进去）
        check('端口在 1..65535 范围',
              all(1 <= p <= 65535 for p in ini + s7),
              'ini=%r s7=%r' % (ini, s7))

    # 两个改写函数必须都存在（文档里承诺的修法）
    check('singleplayer.rewrite_local_ini 存在',
          hasattr(sp, 'rewrite_local_ini'))
    check('singleplayer.rebuild_server7_bin 存在',
          hasattr(sp, 'rebuild_server7_bin'))

    npass = sum(1 for _n, ok in results if ok)
    print('')
    print('=' * 60)
    print('真机端口一致性：%d/%d 通过' % (npass, len(results)))
    if npass != len(results):
        for nm, ok in results:
            if not ok:
                print('  x %s' % nm)
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
