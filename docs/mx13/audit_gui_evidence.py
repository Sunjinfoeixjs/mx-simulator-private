# -*- coding: utf-8 -*-
"""Read-only native GUI screenshot corroboration; NEVER treats screenshots as parse telemetry."""
import hashlib,json,pathlib,re
HERE=pathlib.Path(__file__).resolve().parent
GUI=pathlib.Path(r"C:\Users\Administrator\MX-r006-r007\docs\acceptance_20261024\r007\evidence")
LOG=pathlib.Path(r"C:\Users\Administrator\MX-r006-r007\服务端源码\logs\m4_srv_20261024_144540_s6.log")
MANIFEST=GUI/"manifest.json"
INV=json.loads((HERE/"route_inventory.json").read_text(encoding="utf-8"))
manifest=json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
byname={x["name"]:x for x in manifest}
byop={r["opcode"]:r for r in INV["routes"]}
samples=[
 (96,"mx18_same_account_relogin_world.png","world scene rendered"),
 (16,"mx18_relogin_gui_lv2_exp20.png","character grade and experience panel rendered"),
 (17,"mx18_gui_native_levelup_clicked.png","native level-up button effect"),
 (112,"mx18_gui_battle_started.png","native GUI battle screen"),
 (113,"mx18_gui_battle_auto_action.png","battle turn UI; NOT proof this frame decoded")
]
checks=[]
for opcode,name,label in samples:
    p=GUI/name
    assert name in byname and p.is_file(),name
    raw=p.read_bytes()
    sha=hashlib.sha256(raw).hexdigest()
    assert sha.upper()==byname[name]["sha256"].upper(),name+" SHA256 mismatch"
    assert len(raw)==byname[name]["bytes"]
    assert opcode in byop,opcode
    r=byop[opcode]
    checks.append(dict(opcode=opcode,route_id=r["id"],handler=r["handler"],
      screenshot=name,screenshot_sha256=sha,
      observation=label,evidence_class="GUI_FUNCTIONAL_CORROBORATION_ONLY",
      GUI_PARSE_VERIFIED=False,project_baseline="P1 R006/R007 historical GUI; NOT P0"))
assert LOG.exists(),"P1 GUI session source log not found"
lograw=LOG.read_bytes()
log=lograw.decode("utf-8",errors="replace")
assert "Hello PASS" in log and ("选角进入" in log) and ("进世界" in log)
result=dict(
 title="Prior P1 genuine native GUI evidence cross-referenced to P0 static route IDs",
 status="GUI_FUNCTIONAL_CORROBORATION_ONLY",
 native_gui_screenshot_count=len(checks),
 route_ids_correlated=len(set(c["route_id"] for c in checks)),
 route_parse_confirmed=0,
 reason="No same-session per-S2C-frame success telemetry and no client dispatcher return callback; historical P1 pictures cannot become P0 route parse confirmations.",
 original_screenshot_manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
 historical_server_log_sha256=hashlib.sha256(lograw).hexdigest(),
 historical_server_log_excluded_from_output="contains raw login request/credential data; must not copy",
 evidence=checks
)
(HERE/"GUI_ROUTE_EVIDENCE_CORROBORATION.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
lines=["# MX-13 原生 GUI 路由级证据对账（历史 P1）","",
 "**结论：5 个 GUI 状态的截图及其哈希均匹配，但它们不是同一 P0 会话的 S2C 原始帧 + 客户端解析回调，因此本次可正式计入的 GUI_PARSE_VERIFIED = 0，覆盖率保持 NOT_TESTED。**",
 "",
 "- 源证据：历史 P1 独立分支中的 GUI 截图、SHA256 manifest 和同会话服务器日志；只读取并验证 SHA256。",
 "- 原始服务端日志包含未脱敏登录帧，所以本工具**只存原日志 SHA256**，不会将其内容复制到本分支或 Git。",
 "- 下面按截图所对应的客户端可见功能与路由静态语义建立候选关联，不把它们改称 VERIFIED。",
 "",
 "| Route ID | opcode | GUI 可见效果 | 截图文件 | 严格状态 |",
 "|---|---:|---|---|---|"]
for c in checks:
    lines.append("| %s | %d | %s | %s | 功能佐证，未确认解析 |" %
       (c["route_id"],c["opcode"],c["observation"],c["screenshot"]))
lines += ["",
 "## 继续采集必须具备的证据",
 "- 同一个原生 GUI 会话的 TCP 连接标识、服务器实际送出的 route ID/opcode/帧哈希/顺序。",
 "- 客户端解析层成功返回的 opcode 与 dispatch handler、时间戳及会话/帧唯一编号。",
 "- 帧发射与客户端解析事件可一一对应（加密会话需在解密/LZSS 后比较逻辑帧哈希）。",
 "- 同会话对应的窗口截图/角色存档对照；只有完整链路才能升级为 GUI_PARSE_VERIFIED。",
 "- 没有证据时保持 NOT_TESTED，不能靠现有 screenshot 或 net.py 的 handler 存在性提高 GUI 分子。",
]
(HERE/"GUI_ROUTE_EVIDENCE_CORROBORATION.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"checked_screenshots":len(checks),"sha256_valid":True,"GUI_PARSE_VERIFIED":0,
 "evidence_class":"GUI_FUNCTIONAL_CORROBORATION_ONLY"},ensure_ascii=False))
