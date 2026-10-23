# -*- coding: utf-8 -*-
"""模拟服数据可视化编辑器 —— 本地 HTTP 服务（默认 127.0.0.1:8082）。

职责：
  · 提供单页编辑器 UI（editor.html，内联全部前端资源，无外部依赖）
  · 读写世界数据配置 sim/world_config.json（含校验 / 备份 / 一键还原内置默认）
  · 读写角色存档 save/role.json 的关键字段（位置 / HP / 金币 / 宠物 HP）
  · 代理「运行中的模拟服」GM API（m4_server.py --web），实现实时状态查看与热操作

与其他组件的关系::

    editor.html  ←HTTP→  editor_server.py  →  world_config.json   （世界数据，重启生效）
                                          →  save/role.json       （角色存档）
                                          →  m4 GM API :8080      （实时状态 / 热更新）

用法::

    python editor_server.py                 # 打开 http://127.0.0.1:8082
    python editor_server.py --port 8082 --live http://127.0.0.1:8080
    python editor_server.py --save-dir <dir>

仅使用标准库（http.server / json / urllib），不引入任何第三方依赖。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import world_config  # noqa: E402

CONFIG_PATH = world_config.CONFIG_PATH
SAVE_DIR_DEFAULT = os.path.join(os.path.dirname(HERE), 'save')
HTML_PATH = os.path.join(HERE, 'editor.html')
# 编辑器自身**不**加载 world_config.json 到 m4_server：只有在「还原内置默认」时才
# 以 --no-world-config 语义重新导入 m4_server，取得纯净默认值。
LIVE_URL_DEFAULT = 'http://127.0.0.1:8080'


def _builtin_defaults():
    """取得 m4_server 的**纯净内置默认**（强制忽略 world_config.json）。

    注意：只重新导入 m4_server（它带 --no-world-config 便不会应用配置），
    **不**重载 world_config，以免丢失 CONFIG_PATH 的 --config 覆盖。
    """
    if '--no-world-config' not in sys.argv:
        sys.argv.append('--no-world-config')
    sys.modules.pop('m4_server', None)
    import m4_server
    return world_config.default_config(m4_server)


def _read_role(save_path):
    try:
        with open(save_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_role(save_path, patch):
    """只允许改少量安全字段（位置/HP/金币/宠物HP），其余保持原样。"""
    data = _read_role(save_path) or {}
    allowed = ('px', 'py', 'hero_hp', 'pet_hp', 'coins', 'mapid')
    for k in allowed:
        if k in patch:
            try:
                data[k] = int(patch[k])
            except (TypeError, ValueError):
                raise ValueError('字段 %s 必须是整数' % k)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


class Handler(BaseHTTPRequestHandler):
    save_path = None
    live_url = LIVE_URL_DEFAULT

    # ── 工具 ──
    def _send(self, body, ctype='application/json; charset=utf-8', code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', 0))
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _proxy(self, method, path, payload=None):
        """代理到运行中的模拟服 GM API。

        ★ GM API 是**本机**地址（默认 http://127.0.0.1:8080），必须绕过系统代理：
          环境里一旦有 HTTP_PROXY（公司网络/抓包工具常见），urllib 会把 127.0.0.1
          也送进代理，于是「GM 未启动」的 503 会被换成代理返回的 502/504，
          前端横幅显示成乱七八糟的错误码，问题定位直接被带偏（2026-10-22）。
        """
        url = self.live_url.rstrip('/') + path
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=3) as r:
                return json.loads(r.read().decode('utf-8')), 200
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode('utf-8')), e.code
            except Exception:                      # noqa: BLE001
                return {'error': 'GM API %s' % e.code}, e.code
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {'error': '模拟服 GM API 不可达（%s）：%s' % (url, e)}, 503

    # ── GET ──
    def do_GET(self):
        if self.path in ('/', '/index.html', '/editor.html'):
            try:
                with open(HTML_PATH, 'rb') as f:
                    return self._send(f.read(), 'text/html; charset=utf-8')
            except OSError as e:
                return self._send('<h1>editor.html 读取失败：%s</h1>' % e,
                                  'text/html; charset=utf-8', 500)
        if self.path == '/api/meta':
            cfg = world_config.load_config()
            role = _read_role(self.save_path)
            live, _code = self._proxy('GET', '/api/state')
            return self._send({
                'config_path': CONFIG_PATH,
                'config_exists': os.path.isfile(CONFIG_PATH),
                'save_path': self.save_path,
                'save_exists': role is not None,
                'live_url': self.live_url,
                'live_ok': not (isinstance(live, dict) and live.get('error')),
                'map_names': ([m['name'] for m in cfg['world']['maps']] if cfg else []),
            })
        if self.path == '/api/config':
            cfg = world_config.load_config()
            generated = False
            if cfg is None:
                cfg = _builtin_defaults()
                generated = True
            return self._send({'config': cfg, 'errors': world_config.validate(cfg),
                               'generated': generated})
        if self.path == '/api/config/raw':
            cfg = world_config.load_config() or _builtin_defaults()
            return self._send(json.dumps(cfg, ensure_ascii=False, indent=1),
                              'application/json; charset=utf-8')
        if self.path == '/api/role':
            role = _read_role(self.save_path)
            return self._send({'role': role, 'path': self.save_path})
        if self.path == '/api/live':
            body, code = self._proxy('GET', '/api/state')
            return self._send(body, code=code)
        return self._send({'error': 'not found'}, code=404)

    # ── POST ──
    def do_POST(self):
        p = self.path.strip('/')
        if p == 'api/config':
            ok, errs = world_config.save_config(self._body())
            cfg = world_config.load_config() or {}
            # ★ 保存成功后尝试通知运行中的内核热加载（2026-10-20）。
            #   旧流程是「编辑器只写文件，改完要重启才生效」——用户改完地图/NPC/数值
            #   跑一下发现没变化，以为没保存。现在保存后自动调 GM API 的 reload，
            #   有内核在线就热加载、没有就只返回文件状态（下次启动自然生效）。
            reload = {'tried': False, 'ok': None, 'message': ''}
            if ok:
                body, code = self._proxy('POST', '/api/reload', {})
                reload['tried'] = True
                if code == 200:
                    rb = json.loads(body) if isinstance(body, (str, bytes)) \
                        else body if isinstance(body, dict) else {}
                    reload['ok'] = rb.get('ok', False)
                    reload['message'] = rb.get('message', '')
                    reload['version'] = rb.get('version', 0)
                else:
                    reload['message'] = '内核未在线或不支持热加载（重启后生效）'
            return self._send({'ok': ok, 'errors': errs, 'config': cfg,
                              'reload': reload}, code=200 if ok else 400)
        if p == 'api/config/reset':
            cfg = _builtin_defaults()
            ok, errs = world_config.save_config(cfg)
            # 还原内置默认也通知内核重载
            reload = {'tried': False, 'ok': None, 'message': ''}
            if ok:
                body, code = self._proxy('POST', '/api/reload', {})
                reload['tried'] = True
                if code == 200:
                    rb = json.loads(body) if isinstance(body, (str, bytes)) \
                        else body if isinstance(body, dict) else {}
                    reload['ok'] = rb.get('ok', False)
                    reload['message'] = rb.get('message', '')
            return self._send({'ok': ok, 'errors': errs, 'config': cfg,
                              'reload': reload}, code=200 if ok else 400)
        if p == 'api/config/delete':
            try:
                if os.path.isfile(CONFIG_PATH):
                    os.remove(CONFIG_PATH)
                return self._send({'ok': True,
                                   'config': _builtin_defaults(), 'errors': []})
            except OSError as e:
                return self._send({'ok': False, 'errors': [str(e)]}, code=400)
        if p == 'api/role':
            try:
                data = _write_role(self.save_path, self._body())
                return self._send({'ok': True, 'role': data})
            except (ValueError, OSError) as e:
                return self._send({'ok': False, 'errors': [str(e)]}, code=400)
        if p.startswith('api/live/'):
            action = p[len('api/live/'):]
            body, code = self._proxy('POST', '/api/' + action, self._body())
            return self._send(body, code=code)
        return self._send({'error': 'not found'}, code=404)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description='模拟服数据可视化编辑器')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8082)
    ap.add_argument('--live', default=LIVE_URL_DEFAULT,
                    help='运行中的模拟服 GM API 地址（默认 %s）' % LIVE_URL_DEFAULT)
    ap.add_argument('--save-dir', default=SAVE_DIR_DEFAULT,
                    help='角色存档目录（默认 %s）' % SAVE_DIR_DEFAULT)
    ap.add_argument('--config', default=None,
                    help='世界配置文件路径（默认 %s；隔离测试时用）' % CONFIG_PATH)
    args = ap.parse_args()

    if args.config:
        world_config.CONFIG_PATH = os.path.abspath(args.config)
    Handler.save_path = os.path.join(args.save_dir, 'role.json')
    Handler.live_url = args.live
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('模拟服数据可视化编辑器 → http://%s:%d' % (args.host, args.port))
    print('  世界配置 : %s（%s）' % (world_config.CONFIG_PATH,
                                     '已存在' if os.path.isfile(world_config.CONFIG_PATH)
                                     else '未生成，将用内置默认'))
    print('  角色存档 : %s' % Handler.save_path)
    print('  实时 GM  : %s（未启动则相关面板置灰）' % Handler.live_url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
