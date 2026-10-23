# -*- coding: utf-8 -*-
"""
《梦想世界》server.ini 三层解码模拟器 (2026-10-14 完整版)
=========================================================

复现并验证客户端 login/selectserver.py 的 server.ini 读取管线。

文件三层结构 (已全部破解):
    第1层  容器:  b'-1\\r\\n' + zlib( flag(1B) + len(4B LE) + payload )
    第2层  编码:  payload[i] = (plain[i] + S[i % 9]) & 0xFF     ← 模256加法cipher
                S = [0,9,16,21,24,25,24,21,16]  (即 S[p] = p*(10-p))
                推导: 填充段原始图案 "=FMRUVURM" = '=' + S, 明文为 51 个 '='
    第3层  文本:  GBK 编码, \\r\\n 分行, 空白分字段

已验证: GBK 全量解码成功, 输出为完整服务器列表 (区域/服务器名/IP/端口/线路/标记/编号)
"""
import struct, zlib, sys, os, collections

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # Claw/
INI = r'D:\mxsj2013\client\server.ini'
# 加法密钥 (由 '=' 填充段 KPA 推导, 并用全部输出正确性验证)
SKEY = [0, 9, 16, 21, 24, 25, 24, 21, 16]


def decode_file(path=INI):
    """三层解码, 返回 (原始文件, 解压流, flag, 长度, payload, 明文文本)"""
    d = open(path, 'rb').read()
    assert d[:4] == b'-1\r\n', '不是梦想世界 server.ini (缺少 -1\\r\\n 魔数)'
    r = zlib.decompress(d[4:])
    flag = r[0]
    n = struct.unpack('<I', r[1:5])[0]
    payload = r[5:5 + n]
    assert len(payload) == n
    plain = bytes((b - SKEY[i % 9]) & 0xFF for i, b in enumerate(payload))
    text = plain.decode('gbk')
    return d, r, flag, n, payload, plain, text


def parse_servers(text):
    """按 AliasServerName/Connect 的字段模型解析服务器列表"""
    regions = []          # (区域名, [服务器...])
    cur = None
    others = []           # 代理/更新/IP 行等
    for raw in text.split('\r\n'):
        f = raw.split()
        if not f:
            continue
        if len(f) == 1:
            if set(f[0]) == {'='}:
                continue                      # '=' 分隔线
            cur = (f[0], [])
            regions.append(cur)
        elif len(f) >= 3 and f[0] == 'ip':
            others.append(('更新/代理', f))
        elif len(f) >= 4:
            name, ip = f[0], f[1]
            if ':' in ip:                     # ip:port 形式
                ip, port = ip.split(':', 1)
            else:
                port = f[2] if len(f) > 2 else ''
            line_no = f[3] if len(f) > 3 and f[3].isdigit() else ''
            tag = ''
            alias = ''
            for x in f[4:]:
                if x.startswith('(') and x.endswith(')'):
                    alias = x[1:-1]
                elif x.startswith('·') or x == '&':
                    tag = x
            if cur is None:
                cur = ('(未分区)', [])
                regions.append(cur)
            cur[1].append(dict(name=name, ip=ip, port=port,
                               line=line_no, tag=tag, alias=alias))
        else:
            others.append(('其他', f))
    return regions, others


def main():
    d, r, flag, n, payload, plain, text = decode_file()
    print('=' * 70)
    print('《梦想世界》server.ini 三层解码')
    print('=' * 70)
    print('文件 %d B → 去魔数 → zlib %d B → [flag=0x%02X][len=%d][payload %d B]'
          % (len(d), len(r), flag, n, len(payload)))
    print('第2层: 模256加法cipher, 密钥 S[p]=p*(10-p) =', SKEY)
    print('第3层: GBK 文本 %d B, %d 行' % (len(plain), text.count('\r\n') + 1))
    print()
    regions, others = parse_servers(text)
    total = 0
    for rname, servers in regions:
        print('【%s】' % rname)
        for s in servers:
            mark = (' ' + s['tag']) if s['tag'] else ''
            alias = (' → %s' % s['alias']) if s['alias'] else ''
            print('  %-12s %-16s :%-6s 线路%s%s%s'
                  % (s['name'], s['ip'], s['port'], s['line'], mark, alias))
            total += 1
        print()
    print('-' * 70)
    print('合计: %d 个区域, %d 台服务器' % (len(regions), total))
    if others:
        print('\n其他行 (更新/代理):')
        for kind, f in others:
            print('  [%s] %s' % (kind, ' '.join(f)))
    # 写出清单
    out = []
    for rname, servers in regions:
        out.append('[%s]' % rname)
        for s in servers:
            out.append('%s\t%s\t%s\t%s\t%s\t%s' %
                       (s['name'], s['ip'], s['port'], s['line'], s['tag'], s['alias']))
    open(os.path.join(PROJ, '协议资料', '字典', '服务器列表.txt'), 'w', encoding='utf-8').write('\n'.join(out))
    print('\n已写出 服务器列表.txt')


if __name__ == '__main__':
    main()
