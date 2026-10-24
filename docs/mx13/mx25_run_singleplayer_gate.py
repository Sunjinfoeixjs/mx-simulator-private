# -*- coding: utf-8 -*-
"""Test-only clean singleplayer acceptance gate; never saves login frames."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEST = ROOT / "服务端源码" / "tests" / "_test_singleplayer.py"
OUT = HERE / "MX25_SINGLEPLAYER_CLEAN_REGRESSION_20261025.json"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def execute(repeat):
    assert TEST.is_file() and repeat >= 1
    rows = []
    for i in range(repeat):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        start = time.perf_counter()
        result = subprocess.run([sys.executable, "-B", str(TEST)],
            cwd=str(ROOT), capture_output=True, timeout=70,
            env=env)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        clean = ("Traceback (most recent call last)" not in stdout + stderr
                 and "Exception in thread" not in stdout + stderr)
        assertions = "全部 25 组断言通过" in stdout
        passed = result.returncode == 0 and clean and assertions
        rows.append(dict(run=i+1, status="PASS" if passed else "FAIL",
             exit_code=result.returncode, all_25_assertions_passed=assertions,
             unhandled_traceback=not clean,
             stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
             stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
             elapsed_seconds=round(time.perf_counter()-start, 2)))
        print(json.dumps({k:rows[-1][k] for k in
              ("run","status","exit_code","all_25_assertions_passed",
               "unhandled_traceback","elapsed_seconds")}, ensure_ascii=False))
        if not passed:
            print("Failure output stays local only; inspect in memory if needed.")
            return 1
    report = dict(task="MX-25/MX-28-singleplayer", outcome="PASS",
        description="Current isolated P0 test checkout: genuine wild encounter, "
                    "city return, NPC, persistence, local HTTP and memory transport",
        test_source_sha256=sha(TEST), repeat=repeat, runs=rows,
        note="stdout/stderr (including synthetic login inputs) are not stored",
        full_regression_is_separate=True,
        accepted_S2C_sendall_routes_unchanged=44,
        native_GUI_parse="NOT_TESTED",
        recorded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                   encoding="utf-8")
    print("WROTE", OUT.name, "TEST_CODE_SHA256", report["test_source_sha256"])
    return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat",type=int,default=2)
    args = ap.parse_args()
    raise SystemExit(execute(args.repeat))
