"""Slice 4B offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice4b as c

FIXTURES = c.ROOT / "fixtures/aw001_slice4/4b"
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


class BaselineBranchTests(unittest.TestCase):
    def test_baseline_versions_are_explicit_and_chained(self):
        domain = fixture("pt_baseline_update")["after"]["domain_objects"]
        rows = sorted(domain["baseline_snapshots"], key=lambda row: row["version_seq"])
        self.assertEqual([row["version_seq"] for row in rows], [1, 2])
        self.assertIsNone(rows[0]["supersedes_baseline_id"])
        self.assertEqual(rows[1]["supersedes_baseline_id"], "baseline-1")

    def test_branch_uses_fixed_baseline_and_allowed_parameters(self):
        branch = fixture("pt_branch")["after"]["domain_objects"]["proposal_branches"][0]
        self.assertEqual(branch["status"], "DRAFT")
        self.assertTrue(branch["directly_votable"])
        self.assertEqual(branch["base_baseline_id"], "baseline-1")
        self.assertEqual({row["parameter_key"] for row in branch["parameters"]},
                         {"borrow_ratio"})

    def test_branch_parameters_are_limited_to_allowlist(self):
        data = fixture("pt_rejected_branch_param_not_allowed")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_BRANCH_PARAMETER_NOT_ALLOWED")

    def test_hypothetical_constraint_change_is_not_directly_votable(self):
        branch = fixture("pt_branch_hypothetical")["after"]["domain_objects"][
            "proposal_branches"][0]
        self.assertFalse(branch["directly_votable"])
        self.assertEqual(branch["hypothetical_constraint_changes"][0]["constraint_code"],
                         "CASE_BUDGET_CAP")

    def test_baseline_update_does_not_rewrite_branch_or_operations(self):
        stale = fixture("pt_rejected_freeze_stale_branch")["before"]["domain_objects"]
        self.assertEqual(len(stale["baseline_snapshots"]), 2)
        self.assertEqual(stale["proposal_branches"][0]["base_baseline_version"], 1)
        self.assertEqual(stale["operations"], fixture("pt_branch")["before"]["domain_objects"][
            "operations"])

    def test_freeze_requires_all_gates(self):
        rejected = fixture("pt_rejected_freeze_gates_incomplete")
        self.assertTrue(c.validate_transition(rejected)["is_valid"])
        self.assertEqual(rejected["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_BRANCH_GATE_INCOMPLETE")
        data = fixture("pt_freeze_branch_candidate")
        self.assertEqual(data["after"]["execution_outcome"]["outcome"], "APPLIED")
        branch = data["after"]["domain_objects"]["proposal_branches"][0]
        self.assertEqual(branch["status"], "FROZEN_CANDIDATE")
        self.assertEqual(branch["version_seq"], 2)
        self.assertEqual(branch["freeze_evidence"]["participation_audit_ref"],
                         {"evidence_id": "participation-audit", "revision": 1,
                          "content_sha256": "sha256:" + "a" * 64})

    def test_freeze_creates_no_decision_or_commitment(self):
        before = fixture("pt_freeze_branch_candidate")["before"]["domain_objects"]
        after = fixture("pt_freeze_branch_candidate")["after"]["domain_objects"]
        self.assertEqual(before["operations"], after["operations"])
        self.assertEqual(after["operations"]["commitments"], [])
        self.assertEqual(after["operations"]["tickets"], [])


class AiBoundaryTests(unittest.TestCase):
    def test_session_inputs_are_limited_to_ai_permitted_markings(self):
        session = fixture("pt_open_ai_session")["after"]["domain_objects"]["ai_sessions"][0]
        self.assertEqual(session["status"], "OPEN")
        self.assertEqual({row["object_id"] for row in session["allowed_inputs"]},
                         {"case-1", "pool-1"})
        self.assertEqual(session["requester_member_id"], "member-ai-user")

    def test_forbidden_ai_marking_and_purpose_are_rejected(self):
        for name in ["pt_rejected_session_forbidden_input",
                     "pt_rejected_session_purpose_not_allowed"]:
            data = fixture(name)
            self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                                 "rejection_error_code"], "ERR_AI_SESSION_INPUT_FORBIDDEN", name)

    def test_cross_case_input_is_rejected(self):
        data = fixture("pt_rejected_session_cross_case_input")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_CROSS_CASE_REFERENCE")

    def test_draft_is_only_a_draft_and_cannot_carry_effective_fields(self):
        draft = fixture("pt_ai_draft")["after"]["domain_objects"]["ai_drafts"][0]
        self.assertEqual(draft["status"], "DRAFT")
        self.assertEqual(draft["confirmations"], [])
        self.assertEqual(set(draft), {"draft_id", "session_id", "tenant_node_id", "case_id",
                                      "output_kind", "content_sha256", "source_inputs",
                                      "status", "confirmations", "created_command_id",
                                      "created_envelope_id"})

    def test_draft_sources_must_be_in_the_session_allowlist(self):
        data = fixture("pt_rejected_draft_source_not_allowed")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_AI_DRAFT_SOURCE_NOT_ALLOWED")

    def test_human_confirmation_is_recorded_without_effective_action(self):
        data = fixture("pt_confirm_ai_draft_accepted")
        draft = data["after"]["domain_objects"]["ai_drafts"][0]
        self.assertEqual(draft["status"], "HUMAN_CONFIRMED")
        self.assertEqual(draft["confirmations"][0]["confirming_member_id"], "member-ai-user")
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])
        self.assertEqual(data["after"]["domain_objects"]["governance_markings"],
                         data["before"]["domain_objects"]["governance_markings"])

    def test_rejected_draft_is_recorded_separately(self):
        draft = fixture("pt_confirm_ai_draft_rejected")["after"]["domain_objects"][
            "ai_drafts"][0]
        self.assertEqual(draft["status"], "HUMAN_REJECTED")
        self.assertEqual(draft["confirmations"][0]["decision"], "REJECTED")

    def test_non_human_confirmation_is_rejected(self):
        data = fixture("pt_rejected_confirm_non_human")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_HUMAN_CONFIRMATION_SUBJECT_MISMATCH")

    def test_revoked_session_blocks_new_drafts(self):
        data = fixture("pt_rejected_draft_session_revoked")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_AI_SESSION_REVOKED")
        session = fixture("pt_revoke_ai_session")["after"]["domain_objects"]["ai_sessions"][0]
        self.assertEqual(session["status"], "REVOKED")
        self.assertIsNotNone(session["revoked_at"])

    def test_no_restore_action_exists(self):
        self.assertNotIn("RESTORE_AI_SESSION", c.ACTIONS)
        self.assertNotIn("REOPEN_AI_SESSION", c.ACTIONS)


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_freeze_branch_candidate", 0),
                           ("ft_schema_effective_action", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice4b.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["FREEZE_BRANCH_CANDIDATE"]["required_validators"].remove(
                    "CHECK_BRANCH_GATES")
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
            data = fixture("pt_baseline_v1")
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

    def test_ai_cannot_supply_effective_or_actor_or_approval_fields(self):
        for name in ["ft_schema_effective_action", "ft_schema_actor_field",
                     "ft_schema_human_approved_field"]:
            result = c.validate_transition(fixture(name))
            self.assertFalse(result["is_valid"], name)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA", name)

    def test_schema_layer_counts_are_stable(self):
        layers = {}
        for expected in CASES.values():
            layers[expected["validation_layer"]] = layers.get(expected["validation_layer"], 0) + 1
        self.assertEqual(layers.get("REGISTRY_LOADER", 0), 3)
        self.assertGreaterEqual(layers.get("OFFLINE_SEMANTIC", 0), 28)


if __name__ == "__main__":
    unittest.main()
