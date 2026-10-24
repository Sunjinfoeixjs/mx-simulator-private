# -*- coding: utf-8 -*-
"""Combine three real socket-send evidence streams without double-counting route IDs."""
import csv,hashlib,json,pathlib,collections
HERE=pathlib.Path(__file__).resolve().parent
inventory=json.loads((HERE/"route_inventory.json").read_text(encoding="utf-8"))
sources=[
 ("plain_login_move_GM","emission_socket_actual.jsonl","mx13_execution_results.json"),
 ("quest_pet_craft_trade","emission_domains_actual.jsonl","emission_domains_results.json"),
 ("xor_lzss","emission_encrypted_actual.jsonl","mx13_crypto_results.json")
]
sent=[];scenarios=[]
for name,event_file,result_file in sources:
 raw=(HERE/event_file).read_bytes()
 events=[json.loads(x) for x in raw.decode("utf-8").splitlines() if x]
 data=json.loads((HERE/result_file).read_text(encoding="utf-8"))
 expected=(data["test_real_socket"]["actual_emitted_frames"] if name=="plain_login_move_GM"
   else data["physical_successful_frames"] if name=="quest_pet_craft_trade"
   else data["successful_encrypted_frames"]+1)
 success=[e for e in events if e.get("status")=="SENT" and e.get("boundary")=="socket.sendall"]
 assert len(success)==expected,(name,len(success),expected)
 assert not [e for e in events if e.get("status") in ("SEND_ERROR","DECODE_ERROR","INCOMPLETE_FRAME")]
 assert all(e.get("route_id") for e in success)
 for e in success:e["_scenario"]=name
 sent.extend(success)
 scenarios.append(dict(name=name,file=event_file,sha256=hashlib.sha256(raw).hexdigest(),
   successful_frames=len(success),unique_routes=len({e["route_id"] for e in success})))
counter=collections.Counter(x["route_id"] for x in sent)
owners=collections.defaultdict(set)
for e in sent:owners[e["route_id"]].add(e["_scenario"])
rows=[]
for r in inventory["routes"]:
 k=r["id"];cnt=counter[k]
 rows.append(dict(route_id=k,opcode=r["opcode"],handler=r["handler"],tag=r["tag"],
    category=r["category"],status="PASS" if cnt else "NOT_TESTED",
    success_frames=cnt,scenarios="|".join(sorted(owners[k])),
    gui_parse_status="NOT_TESTED",exclusion_reason=""))
denom=len(rows);n=sum(r["status"]=="PASS" for r in rows)
assert denom==160 and n==len(counter) and len(set(r["opcode"] for r in rows))==160
assert not inventory["unresolved"] and len(inventory["client_mapping_outside_frozen_160"])==8
data=dict(issue="MX-13",baseline="p0-accepted/15dd282",
    raw_static_routes=denom,applicable_routes=denom,excluded_routes=[],
    emitted_unique_routes=n,automated_coverage_percent=round(100*n/denom,4),
    successful_physical_frames=len(sent),not_emitted_routes=denom-n,
    gui_decoded_routes=None,gui_status="NOT_TESTED",emitted_not_GUI_verified=n,
    confirmed_unreachable=0,reachability_unknown=denom-n,
    client_mapping_extra=inventory["client_mapping_outside_frozen_160"],
    client_mapping_sha256=inventory["client_mapping_sha256"],
    route_list_sha256=inventory["source_list_sha256"],
    scenarios=scenarios,routes=rows)
(HERE/"S2C_EMISSION_COVERAGE_EXPANDED.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (HERE/"S2C_ROUTE_STATUS_EXPANDED.csv").open("w",newline="",encoding="utf-8-sig") as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
report=["# MX-13：160 条 S2C 路由扩大测试",
 "",
 "- 原始静态分母：160，已证实排除：0，调整适用分母：160。",
 "- 真实 TCP 成功发射帧：%d；自动化实际发射路由：%d/160 = %.2f%%。" % (len(sent),n,100*n/denom),
 "- 未发射路由：%d，标记 NOT_TESTED，并非已证明不可达。" % (denom-n),
 "- 原生 GUI 解析覆盖率：NOT_TESTED。不能用 Python 接收端解析或历史 P1 GUI 截图顶替。",
 "- 原版客户端 NET_FUNCTION_NAME 的 8 个清单外映射仅单列，不擅自调整分母。",
 "",
 "## 场景",
 "| 场景 | 实际物理发射帧 | 唯一路由 |",
 "|---|---:|---:|"]
for s in scenarios:report.append("| %s | %d | %d |" %(s["name"],s["successful_frames"],s["unique_routes"]))
report += ["","## 自动化实际发射的路由","| Route ID | opcode | 客户端 handler | 发射次数 | 场景 |","|---|---:|---|---:|---|"]
for r in rows:
 if r["status"]=="PASS":
  report.append("| %s | %s | %s | %d | %s |" %(r["route_id"],r["opcode"],r["handler"],r["success_frames"],r["scenarios"].replace("|",", ")))
report += ["","其余未命中路由及对应 NOT_TESTED 状态见 S2C_ROUTE_STATUS_EXPANDED.csv。",
 "",
 "## 发送/客户端解析边界",
 "- 只在底层 socket.sendall 正常返回后计算；FrameBatcher 未 flush 时不计数，发送失败不计数。",
 "- XOR 加密场景以实际发出的密文字节为观察对象，使用独立 RX 流解密后还原 LZSS 压缩帧，再取真实 opcode。",
 "- 场景涉及登录、GM 战斗、任务接取和查询、宠物查看、制作费用、商店、交易中心提示、矿点查询。",
 "- 加密/LZSS 采用 P0 真实 loopback TCP、Python 模拟客户端和原算法；并非原生 GUI 解码通过。",
 "- 每个场景 JSONL 和 SHA256 在机器报告 scenarios 中登记，可重算并集。未更改业务公式、协议字段、客户端 DLL 或 P0 标签。",
 "",
 "## 剩余阻断",
 "- GUI route-level 解析事件不存在；原生 GUI 功能截图只能作为关联佐证，不纳入 GUI_VERIFIED 分子。",
 "- 剩余 %d 条未触发路由需要继续扩充真实 C2S 与异步通知场景。" %(denom-n)
]
(HERE/"S2C_EMISSION_COVERAGE_EXPANDED.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print(json.dumps({k:data[k] for k in ("raw_static_routes","emitted_unique_routes","automated_coverage_percent","successful_physical_frames","not_emitted_routes","gui_status")},ensure_ascii=False))
