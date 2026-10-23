import os, sys, json, time, tempfile, urllib.request, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, transport
tmp = tempfile.mkdtemp(prefix='mtscen2_')
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None; srv._persist()
srv.serve_background(0, web=True, web_port=18790)
time.sleep(0.6)
port = srv._web_port

# 模拟客户端连接（内存管道）：让 _conn 有值
se, ce = transport.make_memory_pair()
import threading
threading.Thread(target=srv.handle, args=(se,), daemon=True).start()
time.sleep(0.5)
print('connected:', srv._conn is not None)

def post(p, b):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, p), data=json.dumps(b).encode(), headers={'Content-Type':'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=5); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# 任务：接 → 完成（真实奖励链）
srv.tasks[1] = {'state': 1, 'progress': 0}
coins0, exp0 = srv.coins, srv.exp
st, r = post('/api/task/complete', {})
print('task complete:', st, r.get('ok'))
stt = r.get('state',{})
print('  coins %d->%d  exp %d->%d  tasks=%s' % (coins0, stt.get('coins'), exp0, stt.get('exp')-0, stt.get('tasks')))
assert stt.get('coins') == coins0 + 500, '金钱奖励未发'
assert stt.get('exp') == exp0 + 200, '经验奖励未发'
# 任务移除后 tasks 应无 1
assert stt.get('tasks', {}).get(1) is None or stt.get('tasks', {}).get(1, {}).get('state') != 1
print('  (complete 后任务被移除)' if stt.get('tasks', {}).get(1) is None else '  (任务仍显示)')

# 控制台 task 子命令同样走真实链
c = srv.gm_command('task progress 1')
print('console task progress:', c.get('ok'), c.get('msg'))
c2 = srv.gm_command('task state 2')
print('console task state:', c2.get('ok'), c2.get('msg'))
c3 = srv.gm_command('task complete')
print('console task complete:', c3.get('ok'), c3.get('msg'))

ce.close(); srv.stop()
print('ALL OK')