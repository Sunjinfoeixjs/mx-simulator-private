import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O

tmp = '/tmp/oracle' if os.name != 'nt' else os.path.join(os.environ['TEMP'], 'oracle')
os.makedirs(tmp, exist_ok=True)
srv = m.M4Server('127.0.0.1', 0, seed=0x12345678, encounter_every=0, enemies=3,
                 turn_timeout=30, logdir=os.path.join(tmp,'logs'), savedir=os.path.join(tmp,'save'))
srv.log = lambda *a, **k: None
srv._persist()
port = srv.serve_background(0, web=False)
time.sleep(0.3)
print('kernel port', port)

R = O.StepRunner('127.0.0.1', port, timeout=4.0)
R.hello()
# login + select
R.send_c2s(1, login_body())
R.collect(0.4)
R.send_c2s(2, struct.pack('<I', 1))
R.collect(0.6)
print('in world, seq ok')

B = O.BattleOracle(R, m)
# start war via GM API equivalent: walk to trigger
R.step('war_start', 'GM 开战', None, b'', '走 gm_war_start', window=0.8)
print('war_start ok?', R.steps[-1].ok, R.steps[-1].seen_ops)
srv.gm_war_start()
R.collect(0.8)
print('after gm_war_start:', [op for op,_ in R.steps[-1].frames] if R.steps else [])
srv.gm_war_win()
R.collect(0.8)
print('done')
R.close()
srv.stop()