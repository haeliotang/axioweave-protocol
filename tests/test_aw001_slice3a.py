"""Slice 3A offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice3a as c

FIXTURES = c.ROOT / "fixtures/aw001_slice3/3a"
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


class InclusionAuditTests(unittest.TestCase):
    def test_status_truth_table(self):
        window = {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T00:00:00Z"}
        floor = {"metric": "PARTICIPATED", "denominator_basis": "ELIGIBLE_POPULATION",
                 "ratio_min_ppm": 500000}
        good = {"invited": 100, "responded": 70, "participated": 60, "refused": 10,
                "unreachable": 10, "withdrew": 5, "unknown_outcome": 5}
        strata = [{"stratum_id": "s1", "frame_size": 100, "events": good}]
        cases = [
            ("MET", {"kind": "EXACT", "exact_count": 100}, "2026-11-06T00:00:00Z"),
            ("BELOW_REQUIRED_FLOOR", {"kind": "EXACT", "exact_count": 200}, "2026-11-06T00:00:00Z"),
            ("INDETERMINATE_INSUFFICIENT_DATA", {"kind": "UNKNOWN", "reason": "no frame"},
             "2026-11-06T00:00:00Z"),
            ("INDETERMINATE_INSUFFICIENT_DATA",
             {"kind": "INTERVAL", "lower_bound": 90, "upper_bound": 110}, "2026-11-06T00:00:00Z"),
            ("NOT_YET_EVALUATED", {"kind": "EXACT", "exact_count": 100}, "2026-10-15T00:00:00Z"),
        ]
        for expected, population, now in cases:
            status, basis, _ = c.derive_inclusion_status(window, population, floor, good, strata, now)
            self.assertEqual(status, expected, (population, now, status, basis))

    def test_interval_population_is_never_promoted_to_met(self):
        window = {"start": "2026-10-01T00:00:00Z", "end": "2026-10-31T00:00:00Z"}
        floor = {"metric": "PARTICIPATED", "denominator_basis": "ELIGIBLE_POPULATION",
                 "ratio_min_ppm": 500000}
        events = {"invited": 100, "responded": 70, "participated": 60, "refused": 10,
                  "unreachable": 10, "withdrew": 5, "unknown_outcome": 5}
        status, _, ratio = c.derive_inclusion_status(
            window, {"kind": "INTERVAL", "lower_bound": 60, "upper_bound": 120}, floor,
            events, [{"stratum_id": "s1", "frame_size": 100, "events": events}],
            "2026-11-06T00:00:00Z")
        self.assertEqual(status, "INDETERMINATE_INSUFFICIENT_DATA")
        self.assertIsNone(ratio)

    def test_event_accounting_keeps_refusals_and_unreachable_distinct(self):
        events = fixture("pt_audit_met")["command"]["payload"]["events"]
        self.assertEqual(events["invited"], events["responded"] + events["refused"] +
                         events["unreachable"] + events["withdrew"] + events["unknown_outcome"])
        self.assertLessEqual(events["participated"], events["responded"])

    def test_escalation_does_not_release_the_gate(self):
        domain = fixture("pt_escalate_shortfall")["after"]["domain_objects"]
        audit = domain["inclusion_audits"][0]
        self.assertEqual(audit["audit_status"], "BELOW_REQUIRED_FLOOR")
        self.assertEqual(audit["gate_disposition"], "BLOCKED")
        self.assertEqual(len(audit["escalations"]), 1)
        self.assertEqual(domain["operations"], fixture("pt_escalate_shortfall")["before"]["domain_objects"]["operations"])

    def test_disclosure_requires_and_records_both_keys(self):
        domain = fixture("pt_proceed_with_disclosure")["after"]["domain_objects"]
        audit = domain["inclusion_audits"][0]
        self.assertEqual(audit["gate_disposition"], "PROCEED_WITH_DISCLOSURE")
        disclosure = audit["disclosures"][0]
        self.assertEqual(disclosure["preauthorization_ref"]["authorization_id"], "preauth-1")
        self.assertEqual(disclosure["named_review_ref"]["reviewer_member_id"], "member-reviewer")

    def test_deadline_reached_without_authorization_is_not_released(self):
        data = fixture("pt_rejected_proceed_before_deadline")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "REJECTED")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"]["rejection_error_code"],
                         "ERR_INCLUSION_DEADLINE_NOT_REACHED")


class StatementTests(unittest.TestCase):
    def test_translation_stays_unconfirmed_draft_without_confirmation(self):
        domain = fixture("pt_statement_original")["after"]["domain_objects"]
        statement = domain["speaker_statements"][0]
        statuses = {row["translation_id"]: row["status"]
                    for row in statement["representation"]["translations"]}
        self.assertEqual(statuses, {"translation-1": "UNCONFIRMED_DRAFT"})

    def test_confirmation_is_independent_of_display_permission(self):
        data = fixture("pt_confirm_translation")
        statement = data["after"]["domain_objects"]["speaker_statements"][0]
        confirmed = {row["translation_id"]: row["status"]
                     for row in statement["representation"]["translations"]}
        self.assertEqual(confirmed, {"translation-1": "CONFIRMED"})
        display = {row["translation_id"]: row["display_scope"]
                   for row in statement["display_state"]["translations"]}
        self.assertEqual(display, {"translation-1": "PUBLIC"})
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])

    def test_withdraw_translation_falls_back_only_to_public_original(self):
        public = fixture("pt_withdraw_translation_public_original")["after"]["domain_objects"]
        private = fixture("pt_withdraw_translation_private_original")["after"]["domain_objects"]
        self.assertEqual(public["speaker_statements"][0]["display_state"]["translations"][0]["display_scope"],
                         "ORIGINAL_FALLBACK")
        self.assertEqual(private["speaker_statements"][0]["display_state"]["translations"][0]["display_scope"],
                         "MASKED")
        for domain in (public, private):
            self.assertEqual(domain["operations"], fixture("pt_withdraw_translation_public_original")["before"]["domain_objects"]["operations"])

    def test_withdrawal_never_pauses_ballots_or_orders(self):
        data = fixture("pt_withdraw_translation_public_original")
        before, after = data["before"]["domain_objects"], data["after"]["domain_objects"]
        self.assertEqual(before["operations"]["protective_orders"], after["operations"]["protective_orders"])
        self.assertEqual(before["operations"]["protection_envelopes"], after["operations"]["protection_envelopes"])
        self.assertNotIn("ballot", json.dumps(after["operations"]["effectiveness_envelopes"]).lower())

    def test_materiality_ruling_never_blocks_by_itself(self):
        data = fixture("pt_rule_materiality")
        rulings = data["after"]["domain_objects"]["materiality_rulings"]
        self.assertEqual(len(rulings), 1)
        self.assertFalse(rulings[0]["automatic_ballot_pause"])
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])

    def test_confirmation_binds_exact_text_version(self):
        data = fixture("pt_confirm_translation")
        statement = data["after"]["domain_objects"]["speaker_statements"][0]
        confirmation = statement["expression_confirmations"][0]
        version = statement["translations"][0]["versions"][0]
        self.assertEqual(confirmation["text_sha256"], c.text_digest(version["text"]))

    def test_historical_statement_versions_are_not_rewritten(self):
        data = copy.deepcopy(fixture("pt_confirm_translation"))
        statement = data["before"]["domain_objects"]["speaker_statements"][0]
        statement["original_versions"][0]["text"] = "rewritten"
        result = c.validate_transition(data)
        self.assertFalse(result["is_valid"])
        self.assertIn(result["error_code"], {"ERR_STATEMENT_VERSION_CHAIN",
                                             "ERR_CONFIRMATION_TEXT_MISMATCH"})

    def test_duplicate_confirmation_is_rejected(self):
        data = copy.deepcopy(fixture("pt_confirm_translation"))
        statement = data["before"]["domain_objects"]["speaker_statements"][0]
        statement["expression_confirmations"] = [copy.deepcopy(
            fixture("pt_confirm_translation")["after"]["domain_objects"]["speaker_statements"][0]["expression_confirmations"][0])]
        statement["version_seq"] = 2
        result = c.validate_transition(data)
        self.assertFalse(result["is_valid"])
        self.assertIn(result["error_code"], {"ERR_INVALID_DOMAIN_SCHEMA",
                                             "ERR_STATEMENT_VERSION_CHAIN",
                                             "ERR_NEW_OBJECT_ID_CONFLICT"})


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_audit_met", 0), ("ft_statement_client_speaker", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice3a.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["RECORD_INCLUSION_AUDIT"]["required_validators"].remove(
                    "CHECK_PARTICIPATION_LEDGER")
            else:
                candidate["global_lock_order"] = list(reversed(candidate["global_lock_order"]))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "registry.json"
                path.write_text(json.dumps(candidate))
                result = c.check_registry(path)
                self.assertFalse(result["is_valid"])
                self.assertEqual(result["validation_layer"], "REGISTRY_LOADER")

    def test_client_identity_and_ballot_fields_are_closed(self):
        for name in ["ft_statement_client_speaker", "ft_withdraw_claims_ballot_pause",
                     "ft_rule_claims_ballot_pause", "ft_audit_floor_ppm_out_of_range"]:
            result = c.validate_transition(fixture(name))
            self.assertFalse(result["is_valid"], name)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA", name)

    def test_simulation_only(self):
        for key, value in [("mode", "delegated"), ("execution_enabled", True),
                           ("data_classification", "real")]:
            data = fixture("pt_audit_met")
            data["server_context"][key] = value
            result = c.validate_transition(data)
            self.assertFalse(result["is_valid"], key)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA")

    def test_cross_slice_operations_are_untouched_by_all_applied_fixtures(self):
        for name, expected in CASES.items():
            if expected["error_code"] is not None or expected["validation_layer"] == "REGISTRY_LOADER":
                continue
            data = fixture(name)
            if data["after"]["execution_outcome"]["outcome"] != "APPLIED":
                continue
            self.assertEqual(data["after"]["domain_objects"]["operations"],
                             data["before"]["domain_objects"]["operations"], name)


if __name__ == "__main__":
    unittest.main()
