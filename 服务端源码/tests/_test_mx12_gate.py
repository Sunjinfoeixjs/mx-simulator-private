# -*- coding: utf-8 -*-
"""Regression gate for MX-12: strict JSON, shared opcodes, and count arithmetic."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GATE = next(ROOT.rglob("mx12_gate.py"))
spec = importlib.util.spec_from_file_location("mx12_gate", GATE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class MX12Tests(unittest.TestCase):
    def test_production_dictionary_is_strict_and_valid(self):
        result = audit.lint_dictionary()
        self.assertEqual(result["json_entries"], 53)
        self.assertEqual(result["json_unique_opcodes"], 17)
        self.assertEqual(set(result["json_shared_opcodes"]), {"23", "34", "71", "89"})

    def test_duplicate_top_level_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON keys.*?/alpha"):
            audit.strict_json('{"alpha":1,"alpha":2}')

    def test_duplicate_nested_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON keys.*?/outer/size"):
            audit.strict_json('{"outer":{"size":1,"size":2}}')

    def test_duplicate_object_inside_array_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "/rows/0/x"):
            audit.strict_json('{"rows":[{"x":1,"x":2}]}')

    def test_numeric_opcode_reuse_is_not_duplicate_key(self):
        raw = '{"Alpha":{"opcode":89},"Beta":{"opcode":89}}'
        result = audit.strict_json(raw)
        self.assertEqual(set(result), {"Alpha", "Beta"})

    def test_strict_json_rejects_nan_and_infinity(self):
        for sample in ['{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}']:
            with self.subTest(sample=sample):
                with self.assertRaises(ValueError):
                    audit.strict_json(sample)

    def test_bad_json_is_not_silently_accepted(self):
        with self.assertRaises(json.JSONDecodeError):
            audit.strict_json('{"x": }')

    def test_corrupt_dictionary_schema_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "wrong.json"
            file.write_text('{"C2SBad":{"function":"NotSame"}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing"):
                audit.lint_dictionary(file)

    def test_real_reconciliation_is_explained_and_frozen(self):
        result = audit.audit()
        x = result["reconciliation"]
        self.assertEqual((x["legacy_rows"], x["legacy_unique_opcodes"],
                          x["active_unique_opcodes"]), (123, 121, 134))
        self.assertEqual((len(x["new_only_opcodes"]), len(x["old_only_opcodes"]),
                          x["legacy_duplicate_extra_rows"]), (15, 2, 2))
        self.assertEqual(x["accounting"]["explained_delta"], 11)
        expected = audit.strict_json((GATE.parent / "mx12_reconciliation_baseline.json")
                                     .read_text(encoding="utf-8"))
        self.assertEqual(result, expected)
        modified = copy.deepcopy(result)
        modified["reconciliation"]["active_unique_opcodes"] += 1
        self.assertNotEqual(modified, expected)

    def test_legacy_duplicates_are_numeric_aliases_not_json_errors(self):
        x = audit.audit()["reconciliation"]
        repeats = {row["opcode"]: [n["name"] for n in row["entries"]]
                   for row in x["legacy_duplicate_opcodes"]}
        self.assertEqual(set(repeats), {174, 181})
        self.assertEqual(repeats[174], ["C2S_WAR_SUMM_ESCAPE", "GS2C_CLIENTUI"])
        self.assertEqual(repeats[181], ["GS2C_ZHIDAO", "C2S_NPC_TJTRADECENTER"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
