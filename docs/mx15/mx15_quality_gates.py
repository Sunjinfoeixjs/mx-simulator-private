# -*- coding: utf-8 -*-
"""MX-15 gates: strict JSON, frozen dispatch, emitted S2C, incremental / full.

Run:
 python docs/mx15/mx15_quality_gates.py lint
 python docs/mx15/mx15_quality_gates.py incremental --changed path/to/file.py
 python docs/mx15/mx15_quality_gates.py full
 python docs/mx15/mx15_quality_gates.py release
 python docs/mx15/mx15_quality_gates.py selftest
Full=65 historical P0 regression tests; original native GUI remains an
independent release blocker, not a fabricated PASS.
"""
import argparse
import ast
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = ROOT / "docs" / "mx13"
REPORT = ROOT / "协议资料" / "报告"
JSON_GATE = REPORT / "mx12_gate.py"
RECONCILE_BASE = REPORT / "mx12_reconciliation_baseline.json"
DISPATCH = pathlib.Path("服务端源码/tests/sim/m4_server.py")
RUNNER = ROOT / "服务端源码/tests/sim/tests/_run_all.py"
SINGLE = DOC / "mx25_run_singleplayer_gate.py"
RUNTIME = HERE / ".runtime" / "logs"
ORIGINAL_65 = 65
CRITICAL_HANDLERS = (
 "netlogin.S2CHello", "netlogin.S2CLoginAccountInfo",
 "nethero.S2CPropInit", "netmap.S2CMapSceneM",
 "netwar.S2CWarStart", "netwar.S2CWarTurn"
)

class GateError(RuntimeError):
    pass

def require(ok, reason):
    if not ok:
        raise GateError(reason)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def cmd_git(*args):
    r = subprocess.run(["git","-C",str(ROOT),*args],capture_output=True)
    require(r.returncode == 0, "Git baseline command failed")
    return r.stdout

def p0_check():
    sha = cmd_git("rev-parse","p0-accepted^{commit}").decode().strip()
    require(sha.startswith("15dd282"), "Frozen P0 tag changed")
    parent = cmd_git("merge-base","HEAD","p0-accepted^{commit}").decode().strip()
    require(sha == parent, "Worktree not descended from frozen P0")
    return sha

def dispatch_ast(data):
    tree = ast.parse(data)
    methods = [m for cls in tree.body if isinstance(cls,ast.ClassDef)
               and cls.name == "M4Server" for m in cls.body
               if isinstance(m,ast.FunctionDef) and m.name == "_dispatch_locked"]
    require(len(methods) == 1, "M4Server._dispatch_locked not unique")
    return ast.dump(methods[0], include_attributes=False)

def dispatch_check(override=None):
    expected = cmd_git("show","p0-accepted:"+DISPATCH.as_posix())
    actual = override if override is not None else (ROOT/DISPATCH).read_bytes()
    a,b = dispatch_ast(expected),dispatch_ast(actual)
    require(a == b,"P0 _dispatch_locked AST changed without reviewed equivalence")
    return {"status":"PASS", "method":"M4Server._dispatch_locked",
            "p0_ast_sha256":digest(a.encode()),
            "checkout_ast_sha256":digest(b.encode()),
            "scope":"Mechanical behavior freeze, not original-server authenticity"}

def mx12_mod():
    spec=importlib.util.spec_from_file_location("mx15_mx12", JSON_GATE)
    require(spec is not None and spec.loader is not None,"MX12 parser missing")
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def strict_json_sweep():
    parse=mx12_mod().strict_json
    files=[]
    for folder in ("协议资料/字典","协议资料/报告","docs/mx13"):
        path=ROOT/folder
        require(path.is_dir(),"Source JSON directory missing: "+folder)
        files.extend(sorted(path.glob("*.json")))
    require(len(files)>=18,"Unexpectedly few JSON dictionary/report sources")
    hashes={}
    for path in files:
        name=path.relative_to(ROOT).as_posix()
        try:
            body=path.read_bytes()
            parse(body.decode("utf-8-sig"))
        except (ValueError,UnicodeError) as e:
            raise GateError("JSON duplicate/format error in "+name+": "+str(e))
        hashes[name]=digest(body)
    return {"status":"PASS","strict_json_count":len(hashes),
            "all_duplicate_keys_rejected":True,"input_sha256":hashes}

def client_source(explicit):
    if explicit:
        candidates=[pathlib.Path(explicit)]
    elif os.environ.get("MX_CLIENT_SRC"):
        candidates=[pathlib.Path(os.environ["MX_CLIENT_SRC"])]
    else:
        candidates=[ROOT/"客户端源码/src",
                    ROOT.parent/"MX-mx13/客户端源码/src",
                    ROOT.parent/"MX-r006-r007/客户端源码/src"]
    for p in candidates:
        if p.name=="net.py" and p.is_file():
            p=p.parent
        if (p/"net.py").is_file():
            return p
    raise GateError("Client source net.py unavailable: supply --client-src; "
                    "source is outside frozen P0 Git")

def execute(label,command,timeout,steps):
    RUNTIME.mkdir(parents=True,exist_ok=True)
    safe=re.sub("[^A-Za-z0-9_-]","_",label)
    path=RUNTIME/(safe+".log")
    before=time.monotonic()
    try:
        r=subprocess.run(command,cwd=str(ROOT),capture_output=True,timeout=timeout,
                         env={**os.environ,"PYTHONIOENCODING":"utf-8",
                              "PYTHONDONTWRITEBYTECODE":"1"})
        output=r.stdout+b"\n--- stderr ---\n"+r.stderr
        rc=r.returncode
        timed_out=False
    except subprocess.TimeoutExpired as e:
        output=(e.stdout or b"")+b"\n--- timeout stderr ---\n"+(e.stderr or b"")
        rc=124
        timed_out=True
    path.write_bytes(output)
    entry={"name":label,"status":"PASS" if rc==0 else "FAIL",
           "return_code":rc,"timed_out":timed_out,
           "duration_seconds":round(time.monotonic()-before,2),
           "raw_local_ignored_log":path.relative_to(ROOT).as_posix(),
           "log_sha256":digest(output)}
    steps.append(entry)
    require(rc==0,label+" failed rc="+str(rc)+"; see "+str(path))
    return output.decode("utf-8",errors="replace")

def stats(stdout,mode):
    x=re.search(r"=== STATS ===\s*(.*?)\s*=== END STATS ===",stdout,re.S)
    require(x is not None,"Runner STATS block missing")
    fields=dict(re.findall(r"^\s*([a-zA-Z_]+)\s*:\s*(\S+)\s*$",
                           x.group(1),re.M))
    for k in ("steps_total","steps_passed","steps_failed","mode"):
        require(k in fields,"Runner missing "+k)
    total,passed,failed=(int(fields[k]) for k in
                         ("steps_total","steps_passed","steps_failed"))
    require(total>0 and failed==0 and passed==total,
            "Regression cases failed or were dropped")
    require(fields["mode"]==mode,"Regression mode changed")
    if mode=="full":
        require(total==ORIGINAL_65,"Full suite not exactly 65 P0 steps")
    return {"steps_total":total,"steps_passed":passed,
            "steps_failed":failed,"mode":mode}

def s2c_check(steps):
    report=json.loads((DOC/"S2C_EMISSION_COVERAGE_ADJUDICATED.json")
                      .read_text(encoding="utf-8"))
    require(report["accepted_numerator"]==44 and report["denominator"]==160,
            "Accepted 44/160 coverage denominator or numerator drifted")
    require(report["gui_parse_status"]=="NOT_TESTED",
            "Physical emission wrongly certified native GUI parse")
    execute("s2c_44_160_physical_send_evidence",
            [sys.executable,"-B",str(DOC/"adjudicate_44_routes.py"),"--check"],
            60,steps)
    execute("mx25_gui_matrix_not_tested",
            [sys.executable,"-B",str(DOC/"mx25_prepare_native_gui_matrix.py"),"--check"],
            30,steps)
    return {"status":"PASS_EMISSION_ONLY","accepted_routes":44,
            "denominator":160, "native_gui_parser":"NOT_TESTED"}

def native_gui_status(require_native,steps):
    src=DOC/"native_gui_live/server_physical_sent.jsonl"
    dest=DOC/"native_gui_live/native_client_parser.jsonl"
    if not (src.is_file() and dest.is_file()):
        require(not require_native,"RELEASE BLOCKED: same-session native GUI "
                "physical-send and original DLL Unpack/handler events missing")
        return {"status":"NOT_TESTED","verified_routes":None,
                "rate_percent":None,"reason":"Real native client session absent"}
    execute("native_gui_strict_gate",
            [sys.executable,"-B",str(DOC/"gui_parse_acceptance_gate.py")],
            30,steps)
    info=json.loads((DOC/"GUI_ROUTE_PARSE_ACCEPTANCE.json")
                    .read_text(encoding="utf-8"))
    if require_native:
        registry=json.loads((DOC/"route_inventory.json").read_text(encoding="utf-8"))
        required={r["id"] for r in registry["routes"]
                  if r["handler"] in CRITICAL_HANDLERS}
        require(len(required)==len(CRITICAL_HANDLERS),
                "Native GUI release route inventory incomplete")
        proven=set(info.get("verified_route_ids",[]))
        require(required<=proven,"RELEASE BLOCKED: native GUI critical "
                "handlers unproven: "+repr(sorted(required-proven)))
    return {"status":info["status"],"verified_routes":info.get("verified_routes"),
            "rate_percent":info.get("rate_percent")}

def pick_incremental(changed):
    filenames=sorted(set(str(p).replace("\\","/") for p in changed))
    need_single=any("_test_singleplayer.py" in p or
                    "mx25_run_singleplayer_gate.py" in p for p in filenames)
    needs_socket=any(p.endswith("m4_server.py") or
                     p.startswith("服务端源码/tests/sim/") for p in filenames)
    # Do not run the 55-case --fast suite for a pure protocol-dictionary edit:
    # lint and denominator are already mandatory; run relevant protocol and
    # dispatcher smoke. Unknown/mixed changes take the conservative path.
    if filenames and all(p.startswith("协议资料/字典/") for p in filenames):
        selected=["_extract_wire","_c2s_coverage","_test_dispatch"]
        kind="dictionary"
    elif filenames and all(p.startswith("docs/mx13/") for p in filenames):
        selected=["_test_tap"]
        kind="s2c_evidence"
    elif filenames and all(p.startswith("docs/mx15/") for p in filenames):
        selected=["_stats"]
        kind="quality_gate"
    elif filenames and all("_test_singleplayer.py" in p or
                           "mx25_run_singleplayer_gate.py" in p for p in filenames):
        selected=["_test_dispatch"]
        kind="singleplayer_test"
    else:
        selected=["--fast"]
        kind="conservative"
    extra=["_e2e_m4","_test_war2"] if needs_socket else []
    selected.extend(extra)
    return {"changed_paths":filenames, "profile":kind,
            "regression_selectors":selected,
            "extra_socket_tests":extra, "singleplayer":need_single,
            "full_run_required_before_merge":True}

def selftest():
    strict=mx12_mod().strict_json
    samples=['{"a":1,"a":2}','{"x":{"a":1,"a":2}}',
             '{"x":[{"a":1,"a":2}]}','{"n":NaN}',
             '{"n":Infinity}','{"oops": }']
    for s in samples:
        try:
            strict(s)
        except ValueError:
            pass
        else:
            raise GateError("Malformed JSON slipped through")
    actual=(ROOT/DISPATCH).read_bytes()
    changed=actual.replace(b"self.move_count += 1",b"self.move_count += 2",1)
    require(actual!=changed and dispatch_ast(actual)!=dispatch_ast(changed),
            "Dispatcher source mutation was not detected")
    bad="=== STATS ===\nsteps_total: 65\nsteps_passed: 64\nsteps_failed: 1\nmode: full\n=== END STATS ==="
    try:
        stats(bad,"full")
    except GateError:
        pass
    else:
        raise GateError("False positive regression runner not rejected")
    require(len(pick_incremental(["服务端源码/tests/sim/m4_server.py"])
                ["extra_socket_tests"])==2,
            "Incremental core source change omitted socket tests")
    return {"status":"PASS","malformed_json_rejected":len(samples),
            "dispatch_mutation_rejected":True,
            "false_success_stats_rejected":True,"socket_route_selection_verified":True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode",choices=("lint","incremental","full","release","selftest"))
    p.add_argument("--client-src",help="Read-only client snapshot directory")
    p.add_argument("--changed",nargs="*",default=None,
                   help="Explicit changed paths for incremental mode")
    opts=p.parse_args()
    result={"task":"MX-15","mode":opts.mode,
            "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status":"FAIL","evidence":{},"steps":[],
            "baseline":"p0-accepted/15dd282"}
    output=HERE/("MX15_"+opts.mode.upper()+"_RESULT.json")
    try:
        result["evidence"]["p0_tag_commit"]=p0_check()
        if opts.mode=="selftest":
            result["evidence"]["selftest"]=selftest()
        else:
            result["evidence"]["json_sweep"]=strict_json_sweep()
            client=client_source(opts.client_src)
            execute("mx12_strict_json_and_123_134_baseline",
                    [sys.executable,"-B",str(JSON_GATE),"--client-src",str(client),
                     "--verify",str(RECONCILE_BASE)],110,result["steps"])
            execute("mx12_negative_unit_tests",
                    [sys.executable,"-B",
                     str(ROOT/"服务端源码/tests/_test_mx12_gate.py")],
                    110,result["steps"])
            result["evidence"]["dispatch_ast"]=dispatch_check()
            result["evidence"]["s2c"]=s2c_check(result["steps"])
            result["evidence"]["gui"]=native_gui_status(
                opts.mode=="release",result["steps"])
            if opts.mode=="incremental":
                changed=opts.changed
                if changed is None:
                    raw=cmd_git("status","--porcelain=v1").decode(
                        "utf-8",errors="replace")
                    changed=[s[3:] for s in raw.splitlines() if len(s)>3]
                selected=pick_incremental(changed)
                result["evidence"]["incremental_selection"]=selected
                result["evidence"]["incremental_stats"]=[]
                for key in selected["regression_selectors"]:
                    out=execute("incremental_"+key.lstrip("-"),
                        [sys.executable,"-B",str(RUNNER),key],
                        360,result["steps"])
                    mode="fast" if key=="--fast" else "filter:"+key
                    result["evidence"]["incremental_stats"].append(
                        stats(out,mode))
                if selected["singleplayer"]:
                    execute("singleplayer_twice_clean",
                            [sys.executable,"-B",str(SINGLE),"--repeat","2"],
                            160,result["steps"])
            elif opts.mode in ("full","release"):
                stdout=execute("full_original_65",
                        [sys.executable,"-B",str(RUNNER)],750,result["steps"])
                result["evidence"]["full_regression"]=stats(stdout,"full")
                execute("singleplayer_twice_clean",
                        [sys.executable,"-B",str(SINGLE),"--repeat","2"],
                        160,result["steps"])
            result["limits"]=[
                "44/160 is server successful physical send only, not native GUI parse",
                "Native GUI Unpack/handler must be proven by real same-session"
                " events; standard full suite never claims GUI PASS",
                "Release requires six critical native GUI handlers;"
                " original-server authenticity requires separate P8 evidence"]
        gui=result["evidence"].get("gui",{})
        result["status"]=("PASS_WITH_NATIVE_GUI_NOT_TESTED"
                          if gui.get("status")=="NOT_TESTED" else "PASS")
        exit_code=0
    except Exception as exc:
        result["error"]=type(exc).__name__+": "+str(exc)
        print("MX15 FAIL",result["error"],file=sys.stderr)
        exit_code=1
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",
                      encoding="utf-8")
    print(json.dumps({"mode":opts.mode,"status":result["status"],
                      "steps":len(result["steps"]),"report":output.name},
                     ensure_ascii=False))
    return exit_code

if __name__=="__main__":
    sys.exit(main())
