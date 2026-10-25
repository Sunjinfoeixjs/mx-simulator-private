# -*- coding: utf-8 -*-
"""MX-13 extra domain requests over real loopback TCP. No builder-call credit."""
import collections, json, pathlib, shutil, socket, struct, sys, time
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from run_emission_e2e import m, EmissionObserver, install_on_handle, recv_s2c, until, send_c2s, hello, login, require

def run():
    out=HERE/"domains_runtime"
    if out.exists():shutil.rmtree(out)
    (out/"save").mkdir(parents=True)
    (out/"logs").mkdir()
    observer=EmissionObserver(HERE/"route_inventory.json", HERE/"emission_domains_actual.jsonl",
                              "mx13_P0_real_TCP_quest_pet_craft_trade")
    prev=m.FRAME_BATCH;m.FRAME_BATCH=True
    undo=install_on_handle(m,observer)
    server=m.M4Server("127.0.0.1",0,m.SEED_DEFAULT,encounter_every=200,
                      enemies=1,turn_timeout=7,savedir=str(out/"save"),
                      logdir=str(out/"logs"),encrypt=False)
    server.log=lambda line:None;server._save_async=False
    port=server.serve_background(port=0)
    c=None
    traces={}
    try:
        c=socket.create_connection(("127.0.0.1",port),timeout=5)
        op,_=recv_s2c(c);require(op==1,"Hello opcode mismatch")
        c.sendall(hello(m.SEED_DEFAULT))
        send_c2s(c,1,login("mx13domains"))
        _,body=until(c,{2})
        if not body[0]:
            send_c2s(c,3,"领域测试".encode("gbk")[:16].ljust(20,b"\0")+b"\0"*4)
            until(c,{3})
        send_c2s(c,2,struct.pack("<I",1))
        world,_=until(c,{97})
        require(16 in world and 96 in world,"World frames absent")
        traces["world"]=world
        def request(label,opcode,body,expects):
            prior=len(observer.events)
            send_c2s(c,opcode,body)
            seen,received_body=until(c,set(expects))
            traces[label]={"C2S":opcode,"S2C_received":seen,
                            "send_events_snapshot":sum(e.get("status")=="SENT" for e in observer.events[prior:]),
                            "snapshot_note":"Asynchronous observer event append can follow receiver read; authoritative count is post-stop JSONL"}
            require(any(op in expects for op in seen),label+" missed response")
            return received_body
        # Task list: P0 login/new character normally has starter tasks.
        tasks=server.tasks
        task_ids=[int(tid) for tid,item in tasks.items() if server._task_state(int(tid))]
        traces["task_ids_before"]=task_ids
        if task_ids:
            request("quest_list",m.C2S_GET_TASK,b"",[72])
            request("quest_detail",m.C2S_NPC_TASK,struct.pack("<HB",task_ids[0],0),[72])
        else:
            traces["quest_list"]="NOT_TESTED_NO_ACTIVE_TASKS"
        # Actual summon id comes from freshly created test role, never fabricate.
        pet_ids=sorted(int(p["pid"]) for p in server.pets if isinstance(p,dict) and "pid" in p) \
            if isinstance(server.pets,list) else sorted(int(k) for k in server.pets)
        traces["pet_ids"]=pet_ids
        if pet_ids:
            request("pet_view",m.C2S_SUMM_VIEW,struct.pack("<I",pet_ids[0]),[51])
        else:
            traces["pet_view"]="NOT_TESTED_NO_PET"
        # Crafting/production: query real path, no crafted response frame.
        request("craft_details",m.C2S_NPC_GETDETAILS,struct.pack("<I",0),[151])
        request("craft_cost",m.C2S_NPC_GETCOST,bytes([0])+struct.pack("<I",0),[155])
        request("minerals",m.C2S_PRODUCE,bytes([m.PRODUCE_SUB_MINERALS]),[87])
        request("trade_center",m.C2S_NPC_TRADECENTER,bytes([1])+struct.pack("<I",0),[152])
        # Local shop NPC context comes from an actual C2S NPC click, not test-side mutation.
        request("merchant_look",m.C2S_NPC_LOOK,struct.pack("<I",m.NPC_ID),[152])
        # Start starter quest through the real NPC menu, not by injecting task state.
        if not task_ids:
            request("quest_accept",m.C2S_NPC_ANSWER,struct.pack("<I",1),[72])
            task_ids=[int(tid) for tid,item in server.tasks.items() if server._task_state(int(tid))]
            traces["task_ids_after_accept"]=task_ids
            require(task_ids,"NPC quest option did not create starter task")
            request("quest_list",m.C2S_GET_TASK,b"",[72])
            request("quest_detail",m.C2S_NPC_TASK,struct.pack("<HB",task_ids[0],0),[72])
        request("merchant_open_shop",m.C2S_NPC_ANSWER,struct.pack("<I",2),[147])
        traces["shop_open_requested"]=True
        # Quest give-up uses the official C2SGiveUpTask request, not a GM shortcut.
        if task_ids:
            request("quest_giveup",m.C2S_GIVEUP_TASK,bytes([0])+struct.pack("<H",task_ids[0]),[73])
        # A dedicated temporary character fixture receives paper and level-5 craft skill.
        # Only the fixture changes; real op102 handles the recipe, costs, items and S2C 160.
        with server._lock:
            server.life_lv=max(5,int(server.life_lv))
            server.huoli=max(100,int(server.huoli))
            server.coins=max(99999,int(server.coins))
        material_id=server.gm_add_item(10155,2,"paper_for_real_TCP_craft_test")
        traces["craft_fixture_material_id"]=material_id
        complete_body=request("craft_complete",m.C2S_NPC_MAKE,bytes([7])+struct.pack("<II",material_id,material_id),[160])
        require(len(complete_body)>=9,"MakeCompleteNotify payload too short")
        crafted_shape,crafted_iid=struct.unpack("<II",complete_body[:8])
        require(crafted_iid in server.items and int(server.items[crafted_iid]["shape"])==crafted_shape,
                "Crafted output does not match authoritative server inventory")
        traces["craft_result"]={"shape":crafted_shape,"iid":crafted_iid,"in_inventory":True}
    finally:
        if c:
            try:c.close()
            except OSError:pass
        server.stop()
        time.sleep(0.2)
        undo();m.FRAME_BATCH=prev
    pending=observer.finalize()
    sent=[e for e in observer.events if e.get("status")=="SENT"]
    errors=[e for e in observer.events if e.get("status") in ("SEND_ERROR","DECODE_ERROR","INCOMPLETE_FRAME")]
    require(pending==0 and not errors and not observer.observation_errors,"Incomplete/failing domain probe")
    actual_routes=sorted({e["route_id"] for e in sent if e.get("route_id")})
    result={"test":"mx13_P0_real_TCP_quest_pet_craft_trade","status":"PASS",
            "physical_successful_frames":len(sent),"unique_routes":len(actual_routes),
            "route_ids":actual_routes,"unmapped":sum(e.get("route_id") is None for e in sent),
            "traces":traces}
    (HERE/"emission_domains_results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":run()
