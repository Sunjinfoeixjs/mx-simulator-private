# -*- coding: utf-8 -*-
"""Snapshot-independent MX-15 merge checks for GitHub-hosted runners.

This is NOT the external-client/P67 full 65-case regression or native GUI gate.
Two MX-12 reconciliation tests require a trusted external client net.py and
must remain BLOCKED, not counted as passed, in this hosted-only profile.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_CLIENT_TESTS = frozenset({
    "test_real_reconciliation_is_explained_and_frozen",
    "test_legacy_duplicates_are_numeric_aliases_not_json_errors",
})


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    mx15 = load_module("mx15_gates", ROOT / "docs/mx15/mx15_quality_gates.py")
    assert mx15.p0_check().startswith("15dd282"), "P0 tag changed"
    mx15.selftest()
    json_result = mx15.strict_json_sweep()
    mx15.dispatch_check()
    mx15.mx12_mod().lint_dictionary()
    print("PASS frozen P0, MX15 selftest, strict JSON ({} files), dispatch AST, MX12 schema"
          .format(json_result["strict_json_count"]), flush=True)

    tests = load_module("mx12_tests", ROOT / "服务端源码/tests/_test_mx12_gate.py")
    found = {name for name in dir(tests.MX12Tests) if name.startswith("test_")}
    if len(found) != 10 or not EXTERNAL_CLIENT_TESTS.issubset(found):
        raise RuntimeError("MX12 inventory changed; review hosted test scope")
    independent = sorted(found - EXTERNAL_CLIENT_TESTS)
    if len(independent) != 8:
        raise RuntimeError("Hosted test count changed unexpectedly")
    print("BLOCKED/NOT_RUN (requires external client snapshot): " +
          ", ".join(sorted(EXTERNAL_CLIENT_TESTS)), flush=True)
    suite = unittest.TestSuite(tests.MX12Tests(n) for n in independent)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun != 8:
        return 1
    print("PASS 8/8 snapshot-independent MX12 tests; "
          "full 65-case regression and native GUI release NOT_TESTED", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
