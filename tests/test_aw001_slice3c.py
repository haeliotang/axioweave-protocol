"""Slice 3C offline contracts: expectations are authored independently of validation."""
import copy
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice3c as c

FIXTURES = c.ROOT / "fixtures/aw001_slice3/3c"
CASES = c.load_json(FIXTURES / "expectations.json")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


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


class BallotIsolationTests(unittest.TestCase):
    def test_ballot_has_no_identity_or_time_fields(self):
        ballot = fixture("pt_cast_ballot")["after"]["domain_objects"]["ballots"][0]
        self.assertEqual(set(ballot), {"ballot_id", "round_id", "option_code", "fingerprint"})
        self.assertEqual(set(ballot["fingerprint"]),
                         {"key_version", "nonce", "hmac_sha256"})
        self.assertNotIn("created_at", ballot)
        self.assertNotIn("member_id", ballot)
        self.assertNotIn("principal_id", ballot)

    def test_ballot_ids_are_random_v4(self):
        for name in ["pt_cast_ballot", "pt_cast_second_ballot", "pt_close_round"]:
            for ballot in fixture(name)["after"]["domain_objects"]["ballots"]:
                self.assertRegex(ballot["ballot_id"], UUID4)

    def test_serial_and_time_ordered_ballot_ids_are_rejected(self):
        for candidate in ["1", "00000000-0000-7000-8000-000000000000"]:
            data = copy.deepcopy(fixture("pt_cast_ballot"))
            data["command"]["payload"]["ballot_id"] = candidate
            result = c.validate_transition(data)
            self.assertFalse(result["is_valid"], candidate)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA", candidate)

    def test_fingerprint_requires_hmac_and_fresh_nonce(self):
        ballot = fixture("pt_cast_ballot")["after"]["domain_objects"]["ballots"][0]
        self.assertRegex(ballot["fingerprint"]["hmac_sha256"], r"^hmac-sha256:[a-f0-9]{64}$")
        self.assertGreaterEqual(len(ballot["fingerprint"]["nonce"]), 32)
        self.assertTrue(ballot["fingerprint"]["hmac_sha256"].startswith("hmac-sha256:"))
        raw = c.raw_option_digest(ballot["round_id"], ballot["option_code"])
        self.assertNotEqual(ballot["fingerprint"]["hmac_sha256"],
                            raw.replace("sha256:", "hmac-sha256:"))

    def test_identity_side_and_content_side_do_not_cross_link(self):
        domain = fixture("pt_cast_ballot")["after"]["domain_objects"]
        for event in domain["eligibility_audit_events"]:
            serialized = json.dumps(event)
            self.assertNotIn("ballot_id", serialized)
            self.assertNotIn("option_code", serialized)
            self.assertNotIn("hmac", serialized)
        for event in domain["ballot_audit_events"]:
            serialized = json.dumps(event)
            self.assertNotIn("member_id", serialized)
            self.assertNotIn("principal_id", serialized)
            self.assertNotIn("command_id", serialized)
            self.assertNotIn("receipt_id", serialized)

    def test_command_result_and_audit_never_expose_ballot_content(self):
        data = fixture("pt_cast_ballot")
        result = data["after"]["execution_outcome"]["command_result"]
        self.assertEqual(result["applied_versions"], [])
        self.assertEqual(result["identity_safe_response"],
                         {"round_id": "round-1", "eligibility_status": "BALLOT_CAST"})
        serialized = json.dumps(result)
        self.assertNotIn("ballot_id", serialized)
        self.assertNotIn("option_code", serialized)
        self.assertNotIn("fingerprint", serialized)
        audit = data["after"]["execution_outcome"]["audit_event"]
        self.assertEqual(audit["written_versions"], [])
        serialized_audit = json.dumps(audit)
        self.assertNotIn("ballot_id", serialized_audit)
        self.assertNotIn("option_code", serialized_audit)
        self.assertNotIn("fingerprint", serialized_audit)

    def test_cast_writes_ballot_and_consumes_eligibility_atomically(self):
        before = fixture("pt_cast_ballot")["before"]["domain_objects"]
        after = fixture("pt_cast_ballot")["after"]["domain_objects"]
        self.assertEqual(len(before["ballots"]), 0)
        self.assertEqual(len(after["ballots"]), 1)
        consumed = [row for row in after["voter_eligibility"] if row["status"] == "CONSUMED"]
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["cast_command_id"],
                         after["eligibility_audit_events"][0]["command_id"])
        self.assertEqual(consumed[0]["cast_receipt_id"],
                         after["eligibility_audit_events"][0]["receipt_id"])

    def test_failed_cast_does_not_consume_eligibility(self):
        data = fixture("pt_rejected_option_invalid")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "REJECTED")
        self.assertEqual(data["after"]["domain_objects"], data["before"]["domain_objects"])
        self.assertFalse(any(row["status"] == "CONSUMED"
                             for row in data["after"]["domain_objects"]["voter_eligibility"]))

    def test_tally_is_derived_from_ballots(self):
        data = fixture("pt_close_round")
        round_row = data["after"]["domain_objects"]["decision_rounds"][0]
        self.assertEqual(round_row["round_status"], "CLOSED")
        tally = round_row["tally"]
        self.assertEqual(tally["ballots_cast"], 2)
        self.assertEqual(tally["consumed_count"], 2)
        self.assertEqual(tally["eligible_count"], 3)
        self.assertEqual({row["option_code"]: row["count"] for row in tally["counts"]},
                         {"A": 1, "B": 1, "C": 0})


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_cast_ballot", 0), ("ft_ballot_created_at", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice3c.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["CAST_BALLOT"]["required_validators"].remove(
                    "CHECK_SECRET_BALLOT_ISOLATION")
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
            data = fixture("pt_open_round")
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

    def test_round_history_is_not_rewritten(self):
        data = copy.deepcopy(fixture("pt_close_round"))
        data["after"]["domain_objects"]["decision_rounds"][0]["eligible_member_ids"].append(
            "member-voter4")
        result = c.validate_transition(data)
        self.assertFalse(result["is_valid"])
        self.assertIn(result["error_code"], {"ERR_WRITE_SET_VIOLATION",
                                             "ERR_ROUND_LEDGER_INCONSISTENT",
                                             "ERR_ROUND_TALLY_MISMATCH"})

    def test_bare_request_digest_for_cast_is_rejected(self):
        result = c.validate_transition(fixture("ft_bare_request_digest"))
        self.assertFalse(result["is_valid"])
        self.assertEqual(result["error_code"], "ERR_BALLOT_REQUEST_DIGEST_UNSAFE")

    def test_schema_layer_counts_are_stable(self):
        layers = {}
        for expected in CASES.values():
            layers[expected["validation_layer"]] = layers.get(expected["validation_layer"], 0) + 1
        self.assertEqual(layers.get("REGISTRY_LOADER", 0), 3)
        self.assertGreaterEqual(layers.get("OFFLINE_SEMANTIC", 0), 20)


if __name__ == "__main__":
    unittest.main()
