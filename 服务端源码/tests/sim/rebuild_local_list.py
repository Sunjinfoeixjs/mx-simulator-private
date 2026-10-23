# -*- coding: utf-8 -*-
"""重建本地服务器列表（M1 单机）。

根因（2026-10-15 定位，src/login/selectserver.py）：
  选服界面 ShowSuggestArea() 解析「推荐区」下的服务器行要求 len(lstr) == 5，
  本地测试服行此前是 6 字段（… ·推荐 33517）→ 触发「服务器列表需要更新」
  模态弹框（点确定会循环刷新），推荐服按钮不显示、不探测。
  且点击推荐服按钮需要 m_ServerInfomation[ip]，推荐区行不填充它 → 必须把
  本地测试服同时放进某个区域列表（本脚本放电信3区）才能正常连接。

修复：
  1. 推荐区两行去掉 ID 列 → 5 字段（不弹框）
  2. 电信3区追加「本地测试服 127.0.0.1 3134 3 &」（区域行 → 可点击）
输出：server7_local.bin（8080 劫持返回）+ 客户端 local.ini。

★ 顶层即执行的坑（2026-10-18 修）：本文件此前是平铺脚本，`import` 会直接
  跑完整个流程并改写客户端配置。现统一收进 main()，仅在 `python
  rebuild_local_list.py` 时执行；import 无任何副作用。

⚠️ local.ini 写入格式（2026-10-18 修）：此前第 3 步把 encode() 后的**加密**文本
  写进 local.ini，但客户端 C_sys.RecordString 是恒等函数、只读**明文 GBK**
  （见 singleplayer.rewrite_local_ini 注释），加密格式会弹「服务器列表需要更新」。
  server7_local.bin 走 HTTP 需要加密格式，local.ini 需要明文——两者格式相反。
  现已分开写出：server7_local.bin 用 encode()，local.ini 用 new_text.encode('gbk')，
  回读校验也按各自格式分别进行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serverini_encode import decode_file, encode  # noqa: E402

CLIENT = r'D:\mxsj2013\client'
SIM = os.path.dirname(os.path.abspath(__file__))


def main():
    flag, plain, text = decode_file(os.path.join(CLIENT, 'server.ini'))
    lines = text.split('\r\n')

    # 1) 推荐区两行去 ID（6 字段 → 5 字段）
    changed = 0
    for i, ln in enumerate(lines):
        f = ln.split()
        if len(f) == 6 and f[0] in ('本地测试服', '天赐福缘'):
            lines[i] = ' '.join(f[:5])
            print('[改] %r -> %r' % (ln, lines[i]))
            changed += 1
    if changed == 0:
        print('提示：未找到 6 字段推荐区行（可能已修复过）')

    # 2) 电信3区（战无不胜 占位行之前）追加区域本地测试服行
    anchor = '战无不胜 111.111.111.111 1111 3 (1月26日) 1111'
    for i, ln in enumerate(lines):
        if ln.strip() == anchor:
            lines.insert(i, '本地测试服 127.0.0.1 3134 3 &')
            print('[加] 电信3区已追加本地测试服区域行')
            break
    else:
        print('提示：未找到电信3区锚点行，跳过区域行追加')

    new_text = '\r\n'.join(lines)

    # 3) 写出两个文件（格式相反，勿混用）
    #    server7_local.bin：走 8080 HTTP 下发，客户端按 server.ini 格式解析 → 需加密
    #    local.ini：客户端 C_sys.RecordString 是恒等函数、只读明文 → 必须明文 GBK
    #               （写加密格式会弹「服务器列表需要更新」）
    srv_bin = os.path.join(SIM, 'server7_local.bin')
    cli_ini = os.path.join(CLIENT, 'local.ini')
    open(srv_bin, 'wb').write(encode(new_text, flag))
    open(cli_ini, 'wb').write(new_text.encode('gbk'))

    # 4) 回读校验（两文件格式不同，分别校验）
    #    注意：推荐区行的端口不固定（会被 singleplayer 按实际监听端口改写），
    #    所以按「字段数 + 存在性」语义校验，不能硬编码端口。
    def _check(txt):
        ls = txt.split('\r\n')
        ok_region = any(ln.startswith('本地测试服') and len(ln.split()) == 5 for ln in ls)
        ok_area = '本地测试服 127.0.0.1 3134 3 &' in txt
        return ok_region, ok_area

    _, _, t_bin = decode_file(srv_bin)
    with open(cli_ini, 'rb') as f:
        t_ini = f.read().decode('gbk')
    for path, txt in ((srv_bin, t_bin), (cli_ini, t_ini)):
        ok_region, ok_area = _check(txt)
        print('[校验] %s 推荐区5字段=%s 区域行=%s' % (path, ok_region, ok_area))
    print('完成。重启游戏客户端即可（刷新会拉 8080 的 server7_local.bin）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
