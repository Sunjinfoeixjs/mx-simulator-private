# -*- coding: utf-8 -*-
"""MX-12: strict duplicate-key JSON lint and audited 123/134 reconciliation."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DICTIONARY = HERE.parent / "字典" / "itempet_opcodes_fields.json"
LEGACY = HERE.parent / "字典" / "protocol_c2s_full.txt"


class Pairs(list):
    pass


def fail_constant(value):
    raise ValueError("nonstandard JSON constant: " + value)


def strict_json(data):
    parsed = json.loads(data, object_pairs_hook=Pairs, parse_constant=fail_constant)
    duplicates = []

    def descend(node, path):
        if isinstance(node, Pairs):
            seen = set()
            out = {}
            for key, value in node:
                sub = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key in seen:
                    duplicates.append(sub)
                seen.add(key)
                out[key] = descend(value, sub)
            return out
        if isinstance(node, list):
            return [descend(value, path + "/" + str(i)) for i, value in enumerate(node)]
        return node

    result = descend(parsed, "")
    if duplicates:
        raise ValueError("duplicate JSON keys: " + ", ".join(duplicates))
    return result


def lint_dictionary(path=DICTIONARY):
    obj = strict_json(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or not obj:
        raise ValueError("root must be a nonempty object")
    groups = collections.defaultdict(list)
    for name, row in obj.items():
        at = "/" + name
        if not name or not isinstance(row, dict):
            raise ValueError(at + " must be an object")
        mandatory = {"function", "opcode", "opcode_const", "module", "total_bytes", "fields"}
        if mandatory - set(row):
            raise ValueError(at + " missing " + repr(sorted(mandatory - set(row))))
        if row["function"] != name:
            raise ValueError(at + "/function is inconsistent")
        if type(row["opcode"]) is not int or not 0 <= row["opcode"] <= 65535:
            raise ValueError(at + "/opcode invalid")
        if not isinstance(row["opcode_const"], str) or not row["opcode_const"]:
            raise ValueError(at + "/opcode_const invalid")
        if not isinstance(row["module"], str) or not row["module"]:
            raise ValueError(at + "/module invalid")
        if type(row["total_bytes"]) is not int or row["total_bytes"] < 0:
            raise ValueError(at + "/total_bytes invalid")
        if not isinstance(row["fields"], list):
            raise ValueError(at + "/fields invalid")
        for i, field in enumerate(row["fields"]):
            if not isinstance(field, dict) or not {"kind", "size", "value"} <= set(field):
                raise ValueError(at + "/fields/" + str(i) + " invalid")
            if type(field["size"]) not in (int, str):
                raise ValueError(at + "/fields/" + str(i) + "/size invalid")
        groups[row["opcode"]].append(name)
    # Different function keys can legitimately share one numeric opcode.
    return {
        "json_entries": len(obj),
        "json_unique_opcodes": len(groups),
        "json_shared_opcodes": {str(op): names for op, names in sorted(groups.items())
                                if len(names) > 1},
        "dictionary_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


CONST_RE = re.compile(r"^\s*((?:C2S|C2GS)_\w+)\s*=\s*(\d+)\s*$", re.M)
USE_RE = re.compile(r"\b(C2S_\w+|C2GS_\w+)\b")
OLD_RE = re.compile(r"^\s*(\d+)\s+((?:C2S|GS2C|C2GS)_\w+)\s+")


def locate_client_src(explicit=None):
    if explicit:
        src = Path(explicit)
        if src.is_file() and src.name == "net.py":
            src = src.parent
        if not (src / "net.py").is_file():
            raise ValueError("--client-src must contain net.py")
        return src
    for root in (REPO, REPO.parent / "MX-r006-r007", REPO.parent / "MX"):
        if not root.exists():
            continue
        for file in root.rglob("net.py"):
            if file.parent.name == "src":
                return file.parent
    raise ValueError("client src not in P0 Git; provide --client-src with net.py")


def source_digest(src):
    sha = hashlib.sha256()
    files = sorted(src.rglob("*.py"))
    for file in files:
        sha.update(file.relative_to(src).as_posix().encode("utf-8"))
        sha.update(b"\0")
        sha.update(hashlib.sha256(file.read_bytes()).digest())
    return sha.hexdigest(), len(files)


def reconcile(src, legacy_path=LEGACY):
    net_path = src / "net.py"
    consts = {}
    for name, opcode in CONST_RE.findall(net_path.read_text(encoding="utf-8-sig")):
        consts.setdefault(name, int(opcode))
    used = set()
    for file in src.rglob("*.py"):
        if file == net_path:
            continue
        used.update(USE_RE.findall(file.read_text(encoding="utf-8", errors="replace")))
    active = {}
    used_names = []
    for name, op in consts.items():
        if name in used:
            active.setdefault(op, name)
            used_names.append(name)
    rows = []
    for line_num, line in enumerate(Path(legacy_path).read_text(encoding="utf-8-sig").splitlines(), 1):
        match = OLD_RE.match(line)
        if match:
            rows.append((int(match.group(1)), match.group(2), line_num))
    legacy = collections.defaultdict(list)
    for op, name, line_num in rows:
        legacy[op].append({"name": name, "line": line_num})
    old_set = set(legacy)
    active_set = set(active)
    repeated = [{"opcode": op, "entries": values}
                for op, values in sorted(legacy.items()) if len(values) > 1]
    extra_rows = sum(len(values) - 1 for values in legacy.values())
    new_only = [{"opcode": op, "name": active[op]} for op in sorted(active_set - old_set)]
    old_only = [{"opcode": op, "entries": legacy[op]} for op in sorted(old_set - active_set)]
    source_hash, module_count = source_digest(src)
    delta = len(active_set) - len(rows)
    if delta != len(new_only) - len(old_only) - extra_rows:
        raise ValueError("reconciliation arithmetic inconsistent")
    return {
        "legacy_rows": len(rows),
        "legacy_unique_opcodes": len(old_set),
        "legacy_duplicate_extra_rows": extra_rows,
        "legacy_duplicate_opcodes": repeated,
        "net_const_names": len(consts),
        "used_const_names": len(used_names),
        "active_unique_opcodes": len(active_set),
        "new_only_opcodes": new_only,
        "old_only_opcodes": old_only,
        "net_py_sha256": hashlib.sha256(net_path.read_bytes()).hexdigest(),
        "client_python_file_count": module_count,
        "client_source_digest_sha256": source_hash,
        "accounting": {
            "active_minus_legacy_rows": delta,
            "new_only_count": len(new_only),
            "old_only_count": len(old_only),
            "duplicate_extra_count": extra_rows,
            "explained_delta": len(new_only) - len(old_only) - extra_rows,
        },
    }


def audit(client_src=None, json_file=DICTIONARY, legacy_file=LEGACY, lint_only=False):
    result = {"json_lint": lint_dictionary(json_file)}
    if not lint_only:
        result["reconciliation"] = reconcile(locate_client_src(client_src), legacy_file)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client-src", help="read-only client source containing net.py")
    p.add_argument("--json-file", default=str(DICTIONARY))
    p.add_argument("--legacy-file", default=str(LEGACY))
    p.add_argument("--lint-only", action="store_true")
    p.add_argument("--verify", help="fail when reviewed baseline differs")
    p.add_argument("--emit", help="save reviewed audit JSON, never rewrites source data")
    args = p.parse_args(argv)
    try:
        data = audit(args.client_src, args.json_file, args.legacy_file, args.lint_only)
        if args.verify:
            expected = strict_json(Path(args.verify).read_text(encoding="utf-8"))
            if expected != data:
                raise ValueError("MX12 baseline drift: review before updating evidence")
        if args.emit:
            Path(args.emit).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        item = data["json_lint"]
        print("MX12 PASS: strict JSON, 0 duplicate keys; entries=%d distinct_numeric_ops=%d"
              % (item["json_entries"], item["json_unique_opcodes"]))
        if "reconciliation" in data:
            rec = data["reconciliation"]
            calc = rec["accounting"]
            print("MX12 COUNTS: old_rows=%d old_unique=%d active_unique=%d; delta=%d = %d - %d - %d"
                  % (rec["legacy_rows"], rec["legacy_unique_opcodes"], rec["active_unique_opcodes"],
                     calc["active_minus_legacy_rows"], calc["new_only_count"],
                     calc["old_only_count"], calc["duplicate_extra_count"]))
            print("NEW_ONLY " + json.dumps(rec["new_only_opcodes"], ensure_ascii=False))
            print("OLD_ONLY " + json.dumps(rec["old_only_opcodes"], ensure_ascii=False))
            print("OLD_DUPLICATES " + json.dumps(rec["legacy_duplicate_opcodes"], ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print("MX12 FAIL: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
