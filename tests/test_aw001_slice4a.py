"""Slice 4A offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice4a as c

FIXTURES = c.ROOT / "fixtures/aw001_slice4/4a"
CASES = c.load_json(FIXTURES / "expectations.json")


def fixture(name):
    return c.load_json(FIXTURES / (name + ".json"))


class GoldenTests(unittest.TestCase):
    pass


def golden(name):
    def test(self):
        expected = CASES[name]
        result = (c.check_registry(FIXTURES / (name + ".json"))
                  if expected["validation_layer"] == "REGISTRY_LOADER"
                  else c.validate_transition(fixture(name)))
        self.assertEqual((result["is_valid"], result["validation_layer"], result["error_code"]),
                         (expected["error_code"] is None, expected["validation_layer"],
                          expected["error_code"]), result)
    return test


for name in CASES:
    setattr(GoldenTests, "test_" + name, golden(name))


class MarkingTests(unittest.TestCase):
    def test_marking_versions_and_case_policy_increment(self):
        data = fixture("pt_tighten_marking")
        domain = data["after"]["domain_objects"]
        markings = domain["governance_markings"]
        self.assertEqual([row["version_seq"] for row in markings], [1, 2])
        self.assertEqual([row["policy_version_seq"] for row in markings], [1, 2])
        policy = domain["case_access_policies"][0]
        self.assertEqual(policy["access_policy_version"], 2)

    def test_tightening_is_monotonic(self):
        markings = fixture("pt_tighten_marking")["after"]["domain_objects"]["governance_markings"]
        old, new = markings[0], markings[1]
        self.assertGreaterEqual(set(new["access_requirements"]),
                                set(old["access_requirements"]))
        self.assertLessEqual(set(new["allowed_purposes"]), set(old["allowed_purposes"]))
        self.assertGreaterEqual(c.AI_RANK[new["ai_processing"]], c.AI_RANK[old["ai_processing"]])
        self.assertGreaterEqual(c.AI_RANK[new["export_policy"]], c.AI_RANK[old["export_policy"]])
        self.assertGreaterEqual(c.RETENTION_RANK[new["retention_class"]],
                                c.RETENTION_RANK[old["retention_class"]])

    def test_relaxation_requires_a_named_review(self):
        data = fixture("pt_relax_marking")
        review = data["command"]["payload"]["relaxation_review"]
        self.assertEqual(review["decision"], "APPROVED")
        self.assertEqual(review["reviewer_power"], "REVIEW_MARKING_RELAXATION")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "APPLIED")

    def test_lineage_endpoints_must_belong_to_case(self):
        data = fixture("pt_rejected_cross_case_edge")
        self.assertTrue(c.validate_transition(data)["is_valid"])
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_CROSS_CASE_REFERENCE")

    def test_derived_markings_are_computed_from_inputs(self):
        effective = fixture("pt_derive_restricted_output")["after"]["domain_objects"][
            "derived_outputs"][0]["effective_markings"]
        self.assertEqual(effective["access_requirements"], ["REVIEWERS"])
        self.assertEqual(effective["allowed_purposes"], ["CASE_ANALYSIS"])
        self.assertEqual(effective["ai_processing"], "RESTRICTED")
        self.assertEqual(effective["export_policy"], "RESTRICTED")
        self.assertEqual(effective["retention_class"], "LONG")
        self.assertEqual(effective["cross_case_policy"], "SINGLE_CASE_ONLY")

    def test_derived_output_never_asserts_fact(self):
        output = fixture("pt_derive_restricted_output")["after"]["domain_objects"][
            "derived_outputs"][0]
        self.assertFalse(output["asserted_as_fact"])
        self.assertEqual(output["expression_status"], "NOT_APPLICABLE")

    def test_derived_purposes_cannot_be_unioned(self):
        data = fixture("pt_rejected_derive_purpose_empty")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "REJECTED")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_DERIVATION_PURPOSE_EMPTY")

    def test_unmarked_input_blocks_derivation(self):
        data = fixture("pt_rejected_derive_missing_markings")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_MARKINGS_MISSING")


class LineageTests(unittest.TestCase):
    def test_lineage_edges_do_not_pause_anything(self):
        data = fixture("pt_dependency_review")
        before = data["before"]["domain_objects"]
        after = data["after"]["domain_objects"]
        self.assertEqual(before["operations"], after["operations"])
        self.assertEqual(before["operations"]["protective_orders"], [])
        self.assertEqual(len(after["lineage_edges"]), 1)
        self.assertEqual(len(after["dependency_reviews"]), 1)
        self.assertEqual(after["dependency_reviews"][0]["status"], "OPEN")

    def test_edge_endpoints_must_be_single_case(self):
        data = fixture("pt_rejected_cross_case_edge")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_CROSS_CASE_REFERENCE")

    def test_cycles_are_rejected(self):
        data = fixture("pt_rejected_lineage_cycle")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_LINEAGE_CYCLE")

    def test_visibility_cannot_exceed_endpoint_markings(self):
        data = fixture("pt_rejected_visibility_leak")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_LINEAGE_VISIBILITY_LEAK")

    def test_internal_edges_allow_restricted_endpoints(self):
        data = fixture("pt_lineage_edge_internal")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "APPLIED")

    def test_public_edges_require_public_endpoints(self):
        data = fixture("pt_lineage_edge_public")
        edge = data["after"]["domain_objects"]["lineage_edges"][0]
        self.assertEqual(edge["visibility"], "PUBLIC")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "APPLIED")

    def test_dependency_review_requires_material_premise(self):
        data = fixture("pt_rejected_review_non_material_edge")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_DEPENDENCY_EDGE_INVALID")

    def test_dependency_review_due_must_be_future(self):
        data = fixture("pt_rejected_due_invalid")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_REVIEW_DUE_INVALID")


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_derive_restricted_output", 0), ("ft_pause_claimed", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice4a.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["RECORD_DERIVED_OUTPUT"]["required_validators"].remove(
                    "CHECK_DERIVED_MARKINGS")
            else:
                candidate["global_lock_order"] = list(reversed(candidate["global_lock_order"]))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "registry.json"
                path.write_text(json.dumps(candidate))
                result = c.check_registry(path)
                self.assertFalse(result["is_valid"])
                self.assertEqual(result["validation_layer"], "REGISTRY_LOADER")

    def test_simulation_only(self):
        for key, value in [("mode", "delegated"), ("execution_enabled", True),
                           ("data_classification", "real")]:
            data = fixture("pt_set_marking_case")
            data["server_context"][key] = value
            result = c.validate_transition(data)
            self.assertFalse(result["is_valid"], key)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA")

    def test_cross_slice_operations_are_untouched(self):
        for name, expected in CASES.items():
            if expected["error_code"] is not None or expected["validation_layer"] == "REGISTRY_LOADER":
                continue
            data = fixture(name)
            if data["after"]["execution_outcome"]["outcome"] != "APPLIED":
                continue
            self.assertEqual(data["after"]["domain_objects"]["operations"],
                             data["before"]["domain_objects"]["operations"], name)

    def test_historical_markings_are_not_rewritten(self):
        data = copy.deepcopy(fixture("pt_tighten_marking"))
        data["after"]["domain_objects"]["governance_markings"][0]["allowed_purposes"] = [
            "CASE_ANALYSIS"]
        result = c.validate_transition(data)
        self.assertFalse(result["is_valid"])
        self.assertIn(result["error_code"], {"ERR_WRITE_SET_VIOLATION",
                                             "ERR_CASE_POLICY_VERSION_MISMATCH",
                                             "ERR_MARKING_UPDATE_NOT_MONOTONIC"})

    def test_client_cannot_supply_effective_markings_or_pause(self):
        for name in ["ft_client_effective_markings", "ft_pause_claimed",
                     "ft_fact_assertion"]:
            result = c.validate_transition(fixture(name))
            self.assertFalse(result["is_valid"], name)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA", name)

    def test_schema_layer_counts_are_stable(self):
        layers = {}
        for expected in CASES.values():
            layers[expected["validation_layer"]] = layers.get(expected["validation_layer"], 0) + 1
        self.assertEqual(layers.get("REGISTRY_LOADER", 0), 3)
        self.assertGreaterEqual(layers.get("OFFLINE_SEMANTIC", 0), 20)


if __name__ == "__main__":
    unittest.main()
