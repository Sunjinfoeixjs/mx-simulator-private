# -*- coding: utf-8 -*-
"""
80 端口本地桩服务 —— hosts 劫持 127.0.0.1 mxupdate.duoyi.com 后的响应端
=========================================================================
职责:
  1. 记录客户端的【所有】请求到 _http80.log（诊断金矿）
  2. 6 个更新 txt -> 用 _upd_cache/ 抓取的真实内容原样回放
  3. /server7.txt -> server7_local.bin（与模拟服 8080 一致）
  4. /blank 及 / -> 空 HTML
  5. 其它 -> 404（但仍记录）
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, '_upd_cache')
LOGF = os.path.join(BASE, '_http80.log')
PORT = 80

TXT_FILES = {'newupdate.txt', 'news.txt', 'service.txt', 'tips.txt',
             'version.txt', 'bulletin.txt'}

try:
    META = json.load(open(os.path.join(CACHE, 'meta.json'), encoding='utf-8'))
except Exception:
    META = {}

BLANK = (b'<!DOCTYPE html><html><head><meta charset="gbk">'
         b'<title>local</title></head><body></body></html>')


def logp(s):
    try:
        with open(LOGF, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), s))
            f.flush()
    except OSError:
        pass
    print(s, flush=True)


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'local-mxupdate'

    def log_message(self, fmt, *args):
        pass  # 用我们自己的日志

    def _respond(self, code, data, ctype='text/plain', extra=None):
        self.send_response(code)
        if data is not None:
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
        else:
            self.send_header('Content-Length', '0')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data and self.command != 'HEAD':
            self.wfile.write(data)

    def _handle(self):
        path = self.path.split('?')[0]
        logp('REQ %s %s  from=%s  UA=%r' % (
            self.command, self.path, self.client_address[0],
            self.headers.get('User-Agent', '')))
        name = path.lstrip('/')
        if name in TXT_FILES:
            fn = os.path.join(CACHE, name)
            if os.path.exists(fn):
                data = open(fn, 'rb').read()
                ctype = META.get(name, {}).get('ctype', 'text/plain')
                self._respond(200, data, ctype)
                logp('  -> 200 replay %s (%d bytes)' % (name, len(data)))
                return
        if name == 'server7.txt':
            fn = os.path.join(BASE, 'server7_local.bin')
            if os.path.exists(fn):
                data = open(fn, 'rb').read()
                self._respond(200, data, 'application/octet-stream')
                logp('  -> 200 server7_local.bin (%d bytes)' % len(data))
                return
        if name in ('', 'blank'):
            self._respond(200, BLANK, 'text/html')
            logp('  -> 200 blank')
            return
        self._respond(404, None)
        logp('  -> 404 (unhandled)')

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle


if __name__ == '__main__':
    os.makedirs(CACHE, exist_ok=True)
    logp('==== _http80 启动 127.0.0.1:%d（缓存 %d 个 txt）====' % (
        PORT, len(TXT_FILES)))
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    srv.serve_forever()
