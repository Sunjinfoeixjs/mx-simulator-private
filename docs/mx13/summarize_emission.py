# -*- coding: utf-8 -*-
"""Deterministic MX-13 coverage from actual socket sendall events."""
import collections,csv,hashlib,json,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent
inv=json.loads((HERE/"route_inventory.json").read_text(encoding="utf-8"))
events=[json.loads(s) for s in (HERE/"emission_socket_actual.jsonl").read_text(encoding="utf-8").splitlines() if s.strip()]
execution=json.loads((HERE/"mx13_execution_results.json").read_text(encoding="utf-8"))
real=[e for e in events if e.get("status")=="SENT" and e.get("boundary")=="socket.sendall"]
unknown=[e for e in real if e.get("route_id") is None]
counter=collections.Counter(e["route_id"] for e in real if e["route_id"])
assert inv["raw_route_count"]==160 and not inv["unresolved"]
assert not unknown, f"{len(unknown)} emitted opcodes not mapped to route"
assert execution["test_real_socket"]["status"]=="PASS"
routes=[]
for r in inv["routes"]:
    emits=[e for e in real if e["route_id"]==r["id"]]
    routes.append(dict(route_id=r["id"],source_line=r["line"],opcode=r["opcode"],
      tag=r["tag"],handler=r["handler"],category=r["category"],
      status="PASS" if emits else "NOT_TESTED",
      count=len(emits),test_name=execution["test_real_socket"]["test"] if emits else "",
      call_path=" <- ".join(emits[0]["call_path"]) if emits else "",
      gui_parse_status="NOT_TESTED",eligibility="INCLUDED",exclusion_reason=""))
denom=len(routes);numerator=sum(x["status"]=="PASS" for x in routes)
assert denom==160 and numerator==len(counter)
auto_ops=set()
for seq in execution["test_real_socket"]["client_received"].values():
    if isinstance(seq,list):
        auto_ops.update(x for x in seq if isinstance(x,int))
auto_received=sorted(x["route_id"] for x in routes if x["opcode"] in auto_ops)
result={
 "issue":"MX-13","baseline":"p0-accepted / 15dd282",
 "counting_rule":"Distinct route ID only after actual socket.sendall returns successfully",
 "raw_static_routes":denom,"applicable_routes":denom,
 "excluded_routes":[],"exclusion_count":0,
 "client_route_mappings":len(inv["routes"]),
 "client_mapping_external_to_P0":True,
 "client_mapping_outside_160":inv.get("client_mapping_outside_frozen_160",[]),
 "client_mapping_sha256":inv["client_mapping_sha256"],
 "route_list_sha256":inv["source_list_sha256"],
 "confirmed_reachable_by_actual_emission":numerator,
 "reachability_undetermined":denom-numerator,
 "confirmed_unreachable":0,
 "automated_emitted_unique_routes":numerator,
 "automated_emission_coverage_percent":round(100*numerator/denom,4),
 "real_socket_sent_frames":len(real),
 "socket_send_failures_in_live_run":sum(e["status"]=="SEND_ERROR" for e in events),
 "decode_errors_in_live_run":sum(e["status"] in ("DECODE_ERROR","INCOMPLETE_FRAME") for e in events),
 "automated_receiver_decoded_unique_routes":len(auto_received),
 "automated_receiver_decoded_route_ids":auto_received,
 "confirmed_gui_decoded_routes":0,
 "gui_metric_status":"NOT_TESTED",
 "gui_emission_coverage_percent":None,
 "emitted_but_not_GUI_verified":numerator,
 "not_emitted_by_this_test":denom-numerator,
 "routes":routes,
 "limitations":[
   "The route tag is not the S2C opcode; client NET_FUNCTION_NAME table maps all 160 unambiguously.",
   "Unseen routes are NOT_TESTED, not proven unreachable.",
   "Official GUI parsed metric has no P0 evidence; N/A is more accurate than claimed 0% coverage.",
   "Only successful real TCP sendall after FrameBatcher flush counts.",
   "Test-only probes never change production source or record raw credential bodies.",
   "P0 test is unencrypted; GUI, crypto mode and extended application domains remain untested.",
 ]}
(HERE/"S2C_EMISSION_COVERAGE.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (HERE/"S2C_ROUTE_STATUS.csv").open("w",newline="",encoding="utf-8-sig") as f:
    w=csv.DictWriter(f,fieldnames=list(routes[0]));w.writeheader();w.writerows(routes)
lines=[
 "# MX-13 / P6-B: 160 条 S2C 路由发射覆盖率",
 "",
 "## 验收数据（P0 隔离基线）",
 "- P0 标签 p0-accepted 保持不变，独立分支 feature/mx-13-s2c-emission。",
 "- 源清单 protocol_s2c_full.txt 共 160 条，160 个 handler，客户端 NET_FUNCTION_NAME 对应 160 个唯一 opcode。",
 "- 第一列 tag 是客户端处理分组标记，不是 opcode。",
 "- 原始分母 **160**，有证据排除 **0**，适用分母 **160**。",
 "- 已完成实际 TCP sendall 成功发射：**%d 帧，%d 条唯一路由**。" % (len(real),numerator),
 "- **自动化发射覆盖率 = %d / %d = %.2f%%**。" % (numerator,denom,100*numerator/denom),
 "- 由 Python 自动化接收端逐帧解析到 **%d 条不同路由**；不是原生 GUI 的解析成功证明。" % len(auto_received),
 "- 原生 GUI 实际发射与成功解析：**NOT_TESTED**（本轮没有 P0 原生 GUI 路由级观测），不能推导出 GUI 实际覆盖率。",
 "- 发射但尚未验证原生 GUI 解析：**%d**；未在本轮自动化测试发射的路由：**%d**（NOT_TESTED，而非不可达）。" % (numerator,denom-numerator),
 "",
 "## 实际发射边界和等价性门禁",
 "- Test-only 观察器只在底层 socket.sendall 正常返回后生成 SENT 事件。",
 "- 合帧 FrameBatcher 缓冲不计数；flush 成功后独立拆分每一帧，扩展长度/半帧跨调用按字节流重组。",
 "- 真实 TCP 连接下确认路由；FakeConn / 内存发送不得记入发射分子。",
 "- 帧 SHA-256、opcode、route ID、测试名、物理调用栈和结果落入 emission_socket_actual.jsonl，不记录原始私密负载。",
 "- 边界测试 PASS：分片、多帧合并、构造但不 flush 不计数、底层写失败不计数且原异常传播；逐字节比较送达数据未改变。",
 "- 实际发射链包括普通 login/world/move/NPC，以及 GM 战斗开始/结束的异步推送。测试服务器在独立存档目录和回环端口运行。",
 "- 源码 m4_server.py 未编辑；业务锁、协议格式、参数和发送顺序未调整。",
 "",
 "## 发射命中的路由",
 "| Route | Opcode | Handler | Frames | Status |",
 "|---|---:|---|---:|---|",
]
for r in routes:
    if r["status"]=="PASS":
        lines.append("| %s | %d | %s | %d | PASS |" %
          (r["route_id"],r["opcode"],r["handler"],r["count"]))
lines += [
 "",
 "其余 %d 条路由逐项标记 NOT_TESTED，见 S2C_ROUTE_STATUS.csv。" % (denom-numerator),
 "",
 "## 静态总量与可达性",
 "- 静态总路由 160；实测可达路由至少 %d；其余 %d 可达性未知。" % (numerator,denom-numerator),
 "- 未发现有证据可排除的别名/不可达项，故原始与适用分母均保持 160。客户端 net.py 额外映射不被隐式并入本次冻结清单。",
 "- 客户端字典另有 %d 项清单外 opcode（仅作审计，不调整 160 分母）：%s。" % (len(inv.get('client_mapping_outside_frozen_160',[])), ', '.join(str(x['opcode'])+' '+x['handler'] for x in inv.get('client_mapping_outside_frozen_160',[]))),
 "- 发射事件不等于客户端成功解析；测试 Python 解码器与官方 GUI 是两个不同统计对象。",
 "",
 "## 复现与限制",
 "- route_inventory.py: 读取源清单和外部客户端 net.py，计算并存储 SHA-256；net.py 不在 P0 2593 文件冻结工作树中。",
 "- emission_probe.py: 仅测试期间注入 socket sendall 观察器，正常服务端无该探针。",
 "- run_emission_e2e.py: 真正 socket 集成及分片/合帧/发送异常验证。",
 "- emission_socket_actual.jsonl / emission_boundary_tests.jsonl: 原始事件。",
 "- mx13_execution_results.json: 真实客户端测试驱动及结果。",
 "- summarize_emission.py: 从原始记录重算 coverage JSON、CSV 和本 Markdown。",
 "- 需要进一步以更多真实 socket 场景覆盖任务、宠物、制作、交易等；不能把现有 26/160 当作游戏完整程度。",
 "- 原版加密/LZSS、跨线程更多推送及实际 GUI 解析另需专项测试，不得凭构建函数存在补分。",
]
(HERE/"S2C_EMISSION_COVERAGE.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"raw":denom,"applicable":denom,"success_frames":len(real),"unique":numerator,"auto_recv_unique":len(auto_received),"GUI":"NOT_TESTED","rate_percent":round(100*numerator/denom,2)},ensure_ascii=False))
