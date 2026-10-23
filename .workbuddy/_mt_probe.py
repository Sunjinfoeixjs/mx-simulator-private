import os, sys, json, time, tempfile, urllib.request
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m
tmp = tempfile.mkdtemp(prefix='mtscen_')
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
srv.serve_background(0, web=True, web_port=18789)
time.sleep(0.7)
port = srv._web_port
def get(p):
    return json.loads(urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, p), timeout=5).read())
def post(p, b):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, p), data=json.dumps(b).encode(), headers={'Content-Type':'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=5); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

maps = get('/api/maps')['maps']
print('maps count:', len(maps))
islands = [mp['mapid'] for mp in maps if mp.get('island')]
print('islands:', sorted(islands))
assert sorted(islands) == [1158, 1159, 1176, 1177]

st, r = post('/api/map', {'mapid': 1158})
stt = r.get('state',{})
print('jump island (no client):', st, 'mapid=', stt.get('mapid'), 'island=', stt.get('map_island'))
assert stt.get('mapid') == 1158 and stt.get('map_island') is True
post('/api/map', {'mapid': 1001})

srv.tasks[1] = {'state': 1, 'progress': 0}
st, r = post('/api/task/progress', {'value': 2})
print('task progress:', st, r.get('state',{}).get('tasks'))
st, r = post('/api/task/state', {'value': 2})
print('task state done:', st, r.get('state',{}).get('tasks'))
c = srv.gm_command('task complete')
print('console task complete:', c.get('ok'), c.get('msg'))
assert c.get('ok') is True
st, r = post('/api/task/state', {'value': 0})
print('task state none:', st, r.get('state',{}).get('tasks'))
assert srv.tasks.get(1) is None

srv._conn = None
st, r = post('/api/task/complete', {})
print('complete no-client:', st, r.get('ok'), r.get('error'))
srv.stop()
print('ALL OK')