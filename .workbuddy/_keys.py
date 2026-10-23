import os, sys, json, time, tempfile, urllib.request
sys.path.insert(0, r'C:\\Users\\Administrator\\MX\\服务端源码\\sim')
import m4_server as m
tmp = tempfile.mkdtemp(prefix='keys_')
srv = m.M4Server('127.0.0.1', 0, savedir=os.path.join(tmp,'s'), logdir=os.path.join(tmp,'l'))
srv.log = lambda *a, **k: None
st = srv.gm_state_dict()
print(sorted(st.keys()))
print('has exp?', 'exp' in st)