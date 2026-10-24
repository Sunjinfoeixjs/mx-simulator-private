# -*- coding: utf-8 -*-
"""MX-13 observed send boundaries: deterministic socket + batch tests, real P0 game socket."""
import collections, hashlib, json, pathlib, select, socket, struct, sys, tempfile, time, shutil
ROOT=pathlib.Path(__file__).resolve().parents[2]
HERE=ROOT/"docs"/"mx13"
SIM=ROOT/"服务端源码"/"tests"/"sim"
sys.path.insert(0,str(SIM))
sys.path.insert(0,str(HERE))
import m4_server as m
from emission_probe import BoundarySocket,EmissionObserver,install_on_handle
INV=HERE/"route_inventory.json"

def require(x,message):
    if not x:raise AssertionError(message)

def exact(sock,n,timeout=4):
    sock.settimeout(timeout);out=b""
    while len(out)<n:
        piece=sock.recv(n-len(out))
        if not piece:raise ConnectionError("closed unexpectedly")
        out+=piece
    return out

def recv_s2c(sock):
    length=exact(sock,1)[0]
    if length==255:length=struct.unpack("<H",exact(sock,2))[0]
    payload=exact(sock,length)
    return payload[0],payload[1:]

def until(sock,wanted,limit=250):
    seen=[]
    for i in range(limit):
        op,body=recv_s2c(sock);seen.append(op)
        if op in wanted:return seen,body
    raise AssertionError("expected opcode not reached: %s, recent=%s"%(wanted,seen[-20:]))

def send_c2s(sock,op,body=b""):
    header=bytes([op])
    if len(body)>=255:header+=b"\xff"+struct.pack("<H",len(body))
    else:header+=bytes([len(body)])
    sock.sendall(header+body)

def hello(seed):
    val=((seed+0x100000000)%3227993+2562088)^6636321
    return bytes([val&255,(val>>8)&255,(val>>16)&255])+b"\0"*22

def login(account="mx13probe",password="local_only"):
    data=bytearray([0,10])+struct.pack("<H",43)
    data+=account.encode("gbk").ljust(32,b"\0")[:32]
    data+=password.encode("gbk").ljust(20,b"\0")[:20]
    data+=bytes([1,2])+struct.pack("<H",73)
    return bytes(data)

def test_send_boundaries():
    result={}
    ob=EmissionObserver(INV,HERE/"emission_boundary_tests.jsonl","mx13_boundary_unit")
    a,b=socket.socketpair()
    try:
        proxy=BoundarySocket(a,ob)
        packet=m.make_s2c(17,b"\x01\x02\x03")
        # An outbound frame isn't emitted until a real successful sendall returns.
        half=len(packet)//2
        proxy.sendall(packet[:half])
        require(not [x for x in ob.events if x["status"]=="SENT"],"partial frame prematurely counted")
        proxy.sendall(packet[half:])
        require(exact(b,len(packet))==packet,"fragmented byte output differed")
        require(sum(x["status"]=="SENT" for x in ob.events)==1,"single reconstructed frame must count once")
        # Batch of 3 frames: zero before flush, three after one socket sendall.
        batch=m.FrameBatcher(proxy)
        frame_set=[m.make_s2c(17,b"\x01"),m.make_s2c(33,b"\x01\x02"),m.make_s2c(112,b"\x00")]
        before=sum(x["status"]=="SENT" for x in ob.events)
        for frame in frame_set: batch.sendall(frame)
        require(sum(x["status"]=="SENT" for x in ob.events)==before,"buffered frames must not count")
        require(batch.batch_stats()["pending"]==len(b"".join(frame_set)),"batch pending wrong")
        require(batch.flush()==len(b"".join(frame_set)),"flush byte count wrong")
        require(exact(b,len(b"".join(frame_set)))==b"".join(frame_set),"coalesced wire differs")
        require(sum(x["status"]=="SENT" for x in ob.events)-before==len(frame_set),"batched emission count wrong")
        result["fragmented_packet"]="PASS"
        result["coalesced_packet"]="PASS"
        result["builder_not_emission"]="PASS"
        # Zero-success socket gives FAIL outcome; must not claim emitted coverage.
        class Failing:
            def sendall(self,data):raise BrokenPipeError("test-simulated failure")
        bad=BoundarySocket(Failing(),ob)
        successful_before=sum(x["status"]=="SENT" for x in ob.events)
        try:bad.sendall(m.make_s2c(72,b"x"))
        except BrokenPipeError:pass
        else:raise AssertionError("failed socket did not raise original error")
        require(sum(x["status"]=="SENT" for x in ob.events)==successful_before,"failed send inflated coverage")
        require(any(x["status"]=="SEND_ERROR" for x in ob.events),"send exception not observed")
        result["failed_send_not_counted"]="PASS"
        # The probe must never turn a successful network send into a failure,
        # or replace an underlying BrokenPipe with its own logging exception.
        class FaultyObserver:
            def __init__(self):self.observation_errors=[]
            def sent(self,*args):raise RuntimeError("observer cannot write")
            def failed_send(self,*args):raise RuntimeError("observer cannot log failure")
        faulty=FaultyObserver()
        fault_proxy=BoundarySocket(a,faulty)
        fault_packet=m.make_s2c(18,b"x")
        require(fault_proxy.sendall(fault_packet) is None,"observer error changed sendall return")
        require(exact(b,len(fault_packet))==fault_packet,"observer error changed transmitted bytes")
        try:BoundarySocket(Failing(),faulty).sendall(fault_packet)
        except BrokenPipeError as exc:
            require(str(exc)=="test-simulated failure","observer replaced original socket exception")
        else:raise AssertionError("observer masked socket exception")
        require(len(faulty.observation_errors)==2,"observer error isolation not recorded")
        result["observation_failure_isolation"]="PASS"
    finally:a.close();b.close()
    require(ob.finalize()==0,"unexpected pending frames")
    result["sent_events"]=sum(x["status"]=="SENT" for x in ob.events)
    return result

def live_socket():
    obs=EmissionObserver(INV,HERE/"emission_socket_actual.jsonl","mx13_p0_real_socket_login_move_gm_battle")
    old=m.FRAME_BATCH
    m.FRAME_BATCH=True
    undo=install_on_handle(m,obs)
    tmp=HERE/"scenario_runtime"
    # Reset only this test-owned directory; never reuse old role state between runs.
    if tmp.exists():shutil.rmtree(tmp)
    (tmp/"save").mkdir(parents=True,exist_ok=True)
    (tmp/"logs").mkdir(parents=True,exist_ok=True)
    server=m.M4Server("127.0.0.1",0,m.SEED_DEFAULT,encounter_every=20,
                      enemies=1,turn_timeout=7,savedir=str(tmp/"save"),logdir=str(tmp/"logs"),encrypt=False)
    server.log=lambda line:None
    server._save_async=False
    port=server.serve_background(port=0)
    c=None
    traces={}
    try:
        c=socket.create_connection(("127.0.0.1",port),timeout=5)
        op,data=recv_s2c(c)
        require(op==1,"missing actual S2C Hello")
        c.sendall(hello(m.SEED_DEFAULT))
        send_c2s(c,1,login())
        seen,body=until(c,{2})
        traces["login"]=seen
        role_exists=bool(body[0]) if body else False
        if not role_exists:
            name="MX13测试"
            send_c2s(c,3,name.encode("gbk")[:16].ljust(20,b"\0")+b"\0"*4)
            seen,_=until(c,{3})
            traces["create_role"]=seen
        send_c2s(c,2,struct.pack("<I",1))
        seen,_=until(c,{97})
        traces["enter_world"]=seen
        require(16 in seen and 96 in seen,"real socket missing PropInit or SceneM")
        # Exercise direct live opcode and flush-after-dispatch.
        send_c2s(c,54,struct.pack("<HH",60,55)+bytes([1,8,0]))
        seen,_=until(c,{86})
        traces["move"]=seen
        # This is an actual C2S request, not a synthetic builder invocation.
        send_c2s(c,96,struct.pack("<I",1000))
        seen,_=until(c,{152})
        traces["npc_look"]=seen
        # GM push is a distinct asynchronous path while socket remains connected.
        r=server.gm_war_start()
        require(r.get("ok"),"GM battle did not start: "+str(r))
        seen,_=until(c,{114})
        traces["gm_war_start"]=seen
        require(112 in seen,"battle-start frame missing over TCP")
        r=server.gm_war_win()
        require(r.get("ok"),"GM battle did not settle: "+str(r))
        seen,_=until(c,{113})
        traces["gm_war_end"]=seen
        require(113 in seen,"battle-end not read over TCP")
        # Send actual game query; output may be intentionally silent for some states.
        send_c2s(c,48)
        # Trigger a real session read flush, without fabricating any response.
        time.sleep(0.4)
        nonblocking_received=0
        c.setblocking(False)
        deadline=time.time()+0.6
        while time.time()<deadline:
            ready,_,_=select.select([c],[],[],0.06)
            if not ready:break
            try:d=c.recv(65536)
            except BlockingIOError:break
            if not d:break
            nonblocking_received+=len(d)
        traces["post_query_received_bytes"]=nonblocking_received
    finally:
        if c:
            try:c.close()
            except OSError:pass
        server.stop()
        time.sleep(0.2)
        undo()
        m.FRAME_BATCH=old
    remain=obs.finalize()
    success=[e for e in obs.events if e["status"]=="SENT"]
    invalid=[e for e in obs.events if e["status"]=="DECODE_ERROR"]
    require(remain==0 and not invalid,"observer frame decode incomplete")
    require(len(success)>0,"no real physical send observations")
    codes=collections.Counter(e["opcode"] for e in success)
    require(codes[1]>0 and codes[16]>0 and codes[112]>0 and codes[113]>0,"expected real socket outcomes missing")
    return dict(test="mx13_p0_real_socket_login_move_gm_battle",status="PASS",
         driver="genuine loopback TCP socket (not fake transport)",client_received=traces,
         actual_emitted_frames=len(success),distinct_opcodes=len(codes),
         observed_route_ids=sorted(set(e["route_id"] for e in success if e["route_id"])),
         observed_opcodes=sorted(codes),
         unmapped_events=sum(1 for e in success if e["route_id"] is None),
         send_errors=len(obs.failures),
         examples=[{"route_id":e["route_id"],"opcode":e["opcode"],
                    "path":e["call_path"]} for e in success[:8]])

def main():
    stats=dict(test_boundary=test_send_boundaries())
    stats["test_real_socket"]=live_socket()
    (HERE/"mx13_execution_results.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"result":"PASS","boundary":stats["test_boundary"],
         "actual_frames":stats["test_real_socket"]["actual_emitted_frames"],
         "unique_routes":len(stats["test_real_socket"]["observed_route_ids"]),
         "unmapped_events":stats["test_real_socket"]["unmapped_events"]},ensure_ascii=False))
if __name__=="__main__":main()
