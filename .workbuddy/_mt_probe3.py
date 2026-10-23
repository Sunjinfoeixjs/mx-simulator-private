import os, sys, json, time, tempfile, urllib.request, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, transport
tmp = tempfile.mkdtemp(prefix='mtscen3_')
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
srv.serve_background(0, web=True, web_port=18791)
time.sleep(0.6)
port = srv._web_port
se, ce = transport.make_memory_pair()
import threading
threading.Thread(target=srv.handle, args=(se,), daemon=True).start()
time.sleep(0.5)
def post(p, b):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, p), data=json.dumps(b).encode(), headers={'Content-Type':'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=5); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

srv.tasks[1] = {'state': 1, 'progress': 0}
coins0, exp0 = srv.coins, srv.exp
st, r = post('/api/task/complete', {})
stt = r.get('state',{})
print('complete ok:', r.get('ok'))
print('coins:', coins0, '->', stt.get('coins'))
print('exp  :', exp0, '->', stt.get('exp'))
assert stt.get('coins') == coins0 + 500
assert stt.get('exp') == exp0 + 200
print('  ✓ 金钱 +500 经验 +200 都生效')

c3 = srv.gm_command('task complete')
print('console complete:', c3.get('ok'), c3.get('msg'))

# 孤岛 + 地图清单再验一次（有客户端时切图会走整包）
maps = json.loads(urllib.request.urlopen('http://127.0.0.1:%d/api/maps' % port, timeout=5).read())['maps']
islands = sorted(mp['mapid'] for mp in maps if mp.get('island'))
print('islands:', islands)
assert islands == [1158, 1159, 1176, 1177]
st, r = post('/api/map', {'mapid': 1176})
print('jump 1176 with conn:', st, r.get('state',{}).get('mapid'), r.get('state',{}).get('mapname'))
assert r.get('state',{}).get('mapid') == 1176

ce.close(); srv.stop()
print('ALL OK')