# -*- coding: utf-8 -*-
"""本地 HTTP 劫持服（M1 单机）：劫持客户端「刷新服务器列表」的 HTTP 请求。

原理：客户端启动时无条件 HTTP 拉 update.ini ServerList 指向的 server7.txt，
官方更新服在线 → 拉官方列表 → 选服界面无本地测试服。
本服务监听 127.0.0.1:8080，GET /server7.txt 返回「本地测试服 127.0.0.1:3134」
编码列表；配合 update.ini ServerList=http://127.0.0.1:8080/server7.txt。

用法:
  python local_http.py            # 监听 127.0.0.1:80（默认 HTTP 端口，供 hosts 劫持）
  python local_http.py --port 80
"""
import argparse
import http.server
import os
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, 'server7_local.bin')   # 本地版编码列表（首行为本地测试服）
DEFAULT_PORT = 80


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path.rstrip('/').lower()
        if path == '/server7.txt':
            if os.path.isfile(BIN):
                data = open(BIN, 'rb').read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                print('[%s] 200 %s → %dB' % (self.log_date_time_string(), self.path, len(data)),
                      flush=True)
            else:
                self.send_response(404)
                self.end_headers()
                print('[%s] 404 %s (缺 %s)' % (self.log_date_time_string(), self.path, BIN),
                      flush=True)
        elif path == '/blank':
            # The launcher uses this local page in place of external HTML pages.
            body = (b'<!DOCTYPE html><html><head><meta charset="gbk">'
                    b'<title>local</title></head><body></body></html>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=gbk')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print('[%s] 200(blank html) %s' % (self.log_date_time_string(), self.path),
                  flush=True)
        else:
            # Do not disguise update manifests or unrelated resources as a server list.
            self.send_error(404, 'No local fixture for this path')
            print('[%s] 404(no local fixture) %s' %
                  (self.log_date_time_string(), self.path), flush=True)

    def log_message(self, fmt, *args):
        pass  # 静默默认访问日志（我们手动打印关键行）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    srv = http.server.HTTPServer(('127.0.0.1', args.port), Handler)
    print('本地 HTTP 劫持服: 监听 127.0.0.1:%d' % args.port, flush=True)
    print('配合 hosts 条目 127.0.0.1 mxupdate.duoyi.com，客户端刷新列表将命中本地', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
