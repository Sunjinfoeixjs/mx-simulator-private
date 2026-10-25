"""Fail-closed public HEAD guard. Never log matched secret strings."""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
filenames = [b.decode("utf-8", "replace") for b in subprocess.check_output(
    ["git","-C",str(ROOT),"ls-files","-z"]).split(b"\x00") if b]
prohibited_prefixes = (".workbuddy/", "docs/acceptance_20261023/")
prohibited_archive_roots = ("1/", "协议资料/字典/")
def unsafe_path(f):
    if f.startswith(prohibited_prefixes):
        return True
    return f.endswith(".zip") and f.startswith(prohibited_archive_roots)
bad_paths = [p for p in filenames if unsafe_path(p)]
if bad_paths:
    print("PUBLIC_REDACTION_FAIL: prohibited tracked artifact paths:", len(bad_paths), file=sys.stderr)
    sys.exit(1)

# This scan is intentionally limited to high-confidence formats; not a full
# historical secrets check and not a private-data/legal-content certification.
patterns = [
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{25,}"),
    re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
]
exts={".py",".ps1",".sh",".txt",".md",".json",".jsonl",".yaml",".yml",
      ".ini",".cfg",".conf",".xml",".csv",".toml",".env",".log",".bat"}
checked=0
detections=[]
for rel in filenames:
    p=ROOT/rel
    if not p.is_file() or p.suffix.lower() not in exts:
        continue
    data=p.read_bytes()
    checked+=1
    if any(rx.search(data) for rx in patterns):
        detections.append(rel)
if detections:
    print("PUBLIC_REDACTION_FAIL: high-confidence secret patterns in",len(detections),
          "tracked file(s); values withheld.",file=sys.stderr)
    sys.exit(1)
print("PUBLIC_REDACTION_HEAD_PASS tracked_paths=",len(filenames),"checked_text_files=",checked)
