"""Slice 3B offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice3b as c

FIXTURES = c.ROOT / "fixtures/aw001_slice3/3b"
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


class CoverageTests(unittest.TestCase):
    def test_insufficient_coverage_is_not_a_violation(self):
        evaluation = fixture("pt_evaluation_insufficient_coverage")["after"]["domain_objects"][
            "metric_evaluations"][0]
        self.assertEqual(evaluation["coverage_status"],
                         "INDETERMINATE_INSUFFICIENT_COVERAGE")
        self.assertEqual(evaluation["result_status"],
                         "INDETERMINATE_INSUFFICIENT_COVERAGE")
        self.assertIsNone(evaluation["direction_outcome"])
        self.assertEqual(evaluation["coverage"]["valid_buckets"], 1)

    def test_dense_sampling_cannot_fill_other_buckets(self):
        evaluation = fixture("pt_evaluation_insufficient_coverage")["after"]["domain_objects"][
            "metric_evaluations"][0]
        self.assertEqual(evaluation["buckets"][0]["valid_sample_count"], 50)
        self.assertEqual(sum(1 for row in evaluation["buckets"] if row["valid"]), 1)
        self.assertLess(evaluation["coverage"]["coverage_ppm"], 900000)

    def test_leading_internal_and_trailing_gaps_are_separate(self):
        evaluation = fixture("pt_evaluation_gap_bounds")["after"]["domain_objects"][
            "metric_evaluations"][0]
        coverage = evaluation["coverage"]
        self.assertEqual((coverage["leading_gap_buckets"], coverage["internal_gap_buckets"],
                          coverage["trailing_gap_buckets"]), (2, 2, 2))
        self.assertEqual(coverage["valid_buckets"], 24)
        self.assertEqual(evaluation["coverage_status"], "COVERAGE_SUFFICIENT")

    def test_window_gaps_are_derived_not_declared(self):
        derived = c.derive_metric_result(
            {"bucket_seconds": 86400, "min_valid_bucket_coverage_ppm": 0,
             "min_samples_per_valid_bucket": 1, "dedupe_rule": "NONE"},
            {"start": "2026-10-01T00:00:00Z", "end": "2026-10-06T00:00:00Z"},
            [{"observation_id": "o1", "observed_at": "2026-10-03T00:00:00Z",
              "received_at": "2026-10-03T00:00:00Z", "value": 1, "quality": "PASS",
              "dedupe_key": None}],
            {"operator": "LT", "threshold_minor": 10},
            {"interval_type": "MEASUREMENT_UNCERTAINTY", "lower_minor": 0,
             "upper_minor": 1, "confidence_ppm": None, "computation_source_ref": {}},
            None, "2026-11-01T00:00:00Z")
        self.assertEqual((derived["coverage"]["leading_gap_buckets"],
                          derived["coverage"]["trailing_gap_buckets"]), (2, 2))

    def test_not_yet_evaluated_is_derived_from_window_end(self):
        evaluation = fixture("pt_evaluation_not_yet_evaluated")["after"]["domain_objects"][
            "metric_evaluations"][0]
        self.assertEqual(evaluation["result_status"], "NOT_YET_EVALUATED")
        self.assertIsNone(evaluation["direction_outcome"])

    def test_duplicate_observations_are_retained_but_not_counted(self):
        evaluation = fixture("pt_evaluation_duplicate_dedupe")["after"]["domain_objects"][
            "metric_evaluations"][0]
        bucket = evaluation["buckets"][0]
        self.assertEqual(bucket["sample_count"], 2)
        self.assertEqual(bucket["duplicate_count"], 1)
        self.assertEqual(bucket["valid_sample_count"], 1)


class DirectionTests(unittest.TestCase):
    def outcome(self, operator, lower, upper, threshold=None, low=None, high=None):
        rule = ({"operator": operator, "threshold_minor": threshold}
                if threshold is not None else
                {"operator": operator, "band_low_minor": low, "band_high_minor": high})
        return c.direction_outcome(rule, {"lower_minor": lower, "upper_minor": upper})

    def test_upper_bound_operators_compare_interval_upper(self):
        self.assertEqual(self.outcome("LT", 60, 90, threshold=100), "SATISFIED")
        self.assertEqual(self.outcome("LT", 95, 105, threshold=100),
                         "INDETERMINATE_UNCERTAINTY")
        self.assertEqual(self.outcome("LT", 110, 120, threshold=100), "VIOLATED")
        self.assertEqual(self.outcome("LTE", 80, 100, threshold=100), "SATISFIED")
        self.assertEqual(self.outcome("LTE", 100, 105, threshold=100),
                         "INDETERMINATE_UNCERTAINTY")

    def test_lower_bound_operators_compare_interval_lower(self):
        self.assertEqual(self.outcome("GT", 105, 120, threshold=100), "SATISFIED")
        self.assertEqual(self.outcome("GT", 95, 105, threshold=100),
                         "INDETERMINATE_UNCERTAINTY")
        self.assertEqual(self.outcome("GT", 80, 95, threshold=100), "VIOLATED")
        self.assertEqual(self.outcome("GTE", 100, 105, threshold=100), "SATISFIED")
        self.assertEqual(self.outcome("GTE", 90, 110, threshold=100),
                         "INDETERMINATE_UNCERTAINTY")

    def test_band_operators_compare_both_bounds(self):
        self.assertEqual(self.outcome("WITHIN_BAND", 95, 105, low=90, high=110), "SATISFIED")
        self.assertEqual(self.outcome("WITHIN_BAND", 80, 120, low=90, high=110),
                         "INDETERMINATE_UNCERTAINTY")
        self.assertEqual(self.outcome("WITHIN_BAND", 120, 130, low=90, high=110), "VIOLATED")
        self.assertEqual(self.outcome("OUTSIDE_BAND", 120, 130, low=90, high=110), "SATISFIED")
        self.assertEqual(self.outcome("OUTSIDE_BAND", 95, 105, low=90, high=110), "VIOLATED")
        self.assertEqual(self.outcome("OUTSIDE_BAND", 105, 120, low=90, high=110),
                         "INDETERMINATE_UNCERTAINTY")

    def test_point_estimate_is_not_used_as_a_fallback(self):
        derived = c.derive_metric_result(
            {"bucket_seconds": 86400, "min_valid_bucket_coverage_ppm": 0,
             "min_samples_per_valid_bucket": 1, "dedupe_rule": "NONE"},
            {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
            [{"observation_id": "o1", "observed_at": "2026-10-01T00:00:00Z",
              "received_at": "2026-10-01T00:00:00Z", "value": 1, "quality": "PASS",
              "dedupe_key": None}],
            {"operator": "LT", "threshold_minor": 10},
            {"interval_type": "MEASUREMENT_UNCERTAINTY", "lower_minor": 9,
             "upper_minor": 11, "confidence_ppm": None, "computation_source_ref": {}},
            0, "2026-11-01T00:00:00Z")
        self.assertEqual(derived["result_status"], "INDETERMINATE_UNCERTAINTY")


class ExtensionTests(unittest.TestCase):
    def test_extension_appends_a_version_and_preserves_older_window(self):
        data = fixture("pt_extend_window_late_data")
        before = data["before"]["domain_objects"]["metric_evaluations"]
        after = data["after"]["domain_objects"]["metric_evaluations"]
        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 2)
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1]["version_seq"], 2)
        self.assertEqual(after[1]["supersedes_evaluation_id"], "evaluation-1")
        self.assertGreater(after[1]["observation_window"]["end"],
                           after[0]["observation_window"]["end"])

    def test_extension_is_rejected_without_matching_prior_evaluation(self):
        data = fixture("pt_rejected_supersedes_mismatch")
        result = c.validate_transition(data)
        self.assertTrue(result["is_valid"], result)
        outcome = data["after"]["execution_outcome"]
        self.assertEqual(outcome["outcome"], "REJECTED")
        self.assertEqual(outcome["command_rejection"]["rejection_error_code"],
                         "ERR_METRIC_SUPERSEDES_MISMATCH")


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_evaluation_satisfied_lt", 0),
                           ("ft_schema_interpolation", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice3b.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["EVALUATE_METRIC_WINDOW"]["required_validators"].remove(
                    "CHECK_METRIC_LEDGER")
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
            data = fixture("pt_define_metric")
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

    def test_historical_evaluations_cannot_be_rewritten(self):
        data = copy.deepcopy(fixture("pt_extend_window_late_data"))
        data["after"]["domain_objects"]["metric_evaluations"][0]["coverage"]["coverage_ppm"] = 1
        result = c.validate_transition(data)
        self.assertFalse(result["is_valid"])
        self.assertIn(result["error_code"], {"ERR_WRITE_SET_VIOLATION",
                                             "ERR_METRIC_LEDGER_INCONSISTENT"})

    def test_invalid_schema_layer_counts_are_stable(self):
        layers = {}
        for name, expected in CASES.items():
            layers[expected["validation_layer"]] = layers.get(expected["validation_layer"], 0) + 1
        self.assertEqual(layers.get("JSON_SCHEMA", 0), 1)
        self.assertGreaterEqual(layers.get("OFFLINE_SEMANTIC", 0), 30)


if __name__ == "__main__":
    unittest.main()
