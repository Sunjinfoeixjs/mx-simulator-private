import os, sys, time, struct
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m, damage_oracle as O

tmp = os.path.join(os.environ['TEMP'], 'oracle')
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
print('enter_world ->', R.enter_world())

B = O.BattleOracle(R, m)
# 开战走真实链路：gm_war_start（serverside）
lo = [op for op,_ in R.collect(0.2)]
res = srv.gm_war_start()
fr = R.collect(0.8)
print('gm_war_start ->', res, 'frames', [op for op,_ in fr])
R.absorb(fr)
print('units:', {k: v['name'] for k,v in B.units.items()})

# 主角普攻第一个敌方
enemy = sorted(w for w in B.units if not B.units[w]['allied'])
print('ally?', {k: v for k,v in B.units.items()})
R.close(); srv.stop()