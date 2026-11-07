"""Synthetic 2B contract assertions; no real authorization or database execution."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice2 as a
from tools import validate_aw001_slice2b as b

ROOT = b.ROOT
FIXTURES = ROOT / "fixtures/aw001_slice2/2b"


def fixture(name):
    return b.load_json(FIXTURES / (name + ".json"))


POSITIVE = [
    "pt_approve_all_phases", "pt_authorize_phase1", "pt_authorize_phase2", "pt_cancel_queued",
    "pt_commit_unknown", "pt_commitment_completed", "pt_confirmed_rollback", "pt_handoff_after_order_expiry",
    "pt_log_phase2", "pt_log_receipt", "pt_log_second_receipt", "pt_reauthorize_expired", "pt_record_handoff", "pt_record_phase2",
    "pt_rejected_authorize_parent_po", "pt_rejected_cancel_after_handoff", "pt_rejected_current_revoked", "pt_rejected_delta_exceeds_claim",
    "pt_rejected_expired_order_metric_unmet", "pt_rejected_handoff_permission_expired", "pt_rejected_member_unmapped",
    "pt_rejected_metric_noncompliant", "pt_rejected_metric_stale", "pt_rejected_new_po_before_handoff", "pt_rejected_origin_revoked",
    "pt_rejected_phase2_partial_only", "pt_rejected_pool_exhausted", "pt_rejected_proposer_approves", "pt_rejected_receipt_already_verified",
    "pt_rejected_relabelled_receipt", "pt_rejected_rules_changed", "pt_rejected_source_and_hold", "pt_rejected_source_unavailable",
    "pt_rejected_template_executor", "pt_rejected_terms_changed", "pt_rejected_verifier_is_finance", "pt_rejected_verifier_is_operator",
    "pt_rejected_wrong_power", "pt_verify_executor_rule_disabled", "pt_verify_insufficient", "pt_verify_not_fulfilled",
    "pt_verify_partial", "pt_verify_phase1_complete",
]
CASES = {name: ("OFFLINE_SEMANTIC", None) for name in POSITIVE}
CASES.update({
    **{name: ("JSON_SCHEMA", "ERR_CLIENT_FORBIDDEN_FIELD") for name in ["ft_client_identity", "ft_collective_approval", "ft_skip_check"]},
    **{name: ("JSON_SCHEMA", "ERR_INCOMPLETE_FOUR_TUPLE") for name in ["ft_missing_remedy", "ft_empty_evidence"]},
    **{name: ("JSON_SCHEMA", "ERR_SCHEMA_VALIDATION") for name in ["ft_money_fraction", "ft_unknown_claims_rollback", "ft_verify_adds_verified_state"]},
    "ft_unknown_action": ("JSON_SCHEMA", "ERR_UNREGISTERED_ACTION_ID"),
    **{name: ("OFFLINE_SEMANTIC", "ERR_APPLIED_WRITE_SET_MISMATCH") for name in [
        "ft_approval_omits_second_ticket", "ft_authorize_claims_handoff", "ft_cancel_reduces_commitment", "ft_cancel_releases_pool",
        "ft_log_counts_fulfilled", "ft_partial_completes_commitment", "ft_reauthorize_rewrites_history", "ft_zero_verdict_changes_commitment_version"]},
    "ft_attempt_generation": ("OFFLINE_SEMANTIC", "ERR_ATTEMPT_GENERATION_MISMATCH"),
    "ft_audit_digest": ("OFFLINE_SEMANTIC", "ERR_AUDIT_BINDING_MISMATCH"),
    **{name: ("OFFLINE_SEMANTIC", "ERR_RECEIPT_BINDING_MISMATCH") for name in ["ft_context_digest", "ft_receipt_attempt", "ft_source_claims_verified"]},
    "ft_dependency_version": ("OFFLINE_SEMANTIC", "ERR_DEPENDENCY_VERSION_MISMATCH"),
    **{name: ("OFFLINE_SEMANTIC", "ERR_DECLARED_OUTCOME_MISMATCH") for name in ["ft_pool_claims_applied", "ft_self_approval_claims_applied"]},
    "ft_rejection_mutates_blocked": ("OFFLINE_SEMANTIC", "ERR_REJECTED_DOMAIN_MUTATION"),
    "ft_rejection_wrong_code": ("OFFLINE_SEMANTIC", "ERR_REJECTION_DIAGNOSTIC_MISMATCH"),
    **{name: ("OFFLINE_SEMANTIC", "ERR_COMMAND_RESULT_MISMATCH") for name in ["ft_result_generation", "ft_retry_terminal"]},
    "ft_target_version": ("OFFLINE_SEMANTIC", "ERR_VERSION_SEQ_MISMATCH"),
    "registry_valid": ("REGISTRY_LOADER", None),
    "registry_missing_validator": ("REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING"),
    "registry_wrong_lock_order": ("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH"),
})


class GoldenTests(unittest.TestCase):
    pass


def golden_test(name):
    def test(self):
        layer, code = CASES[name]
        path = FIXTURES / (name + ".json")
        result = b.check_registry(path) if layer == "REGISTRY_LOADER" else b.validate_file(path)
        self.assertEqual((result["is_valid"], result["validation_layer"], result["error_code"]), (code is None, layer, code), result)
        self.assertEqual(result["failed_path"] is None, code is None)
    return test


for name in CASES:
    setattr(GoldenTests, "test_" + name, golden_test(name))


def refresh_bindings(data):
    """Keep test bookkeeping coherent after changing one substantive condition."""
    cmd, ctx = data["command"], data["server_context"]
    before = data["before"]; out = data["after"]["execution_outcome"]
    before["command_record"]["request_sha256"] = b.digest(cmd)
    if out["command_record"]:
        out["command_record"]["request_sha256"] = b.digest(cmd)
    receipt = ctx["generated_auth_receipt"]
    if receipt is not None:
        receipt["domain_sha256"] = b.digest(before["domain_objects"])
        receipt["context_sha256"] = b.context_digest(ctx)
        out["auth_check_receipt"] = copy.deepcopy(receipt)
        if out["outcome"] == "APPLIED":
            env = data["after"]["domain_objects"]["effectiveness_envelopes"][-1]
            env["auth_check_receipt"] = copy.deepcopy(receipt)
            env["effect_parameters_sha256"] = b.digest(cmd["payload"])
    if out["audit_event"]:
        out["audit_event"]["before_domain_sha256"] = b.digest(before["domain_objects"])
        out["audit_event"]["after_domain_sha256"] = b.digest(data["after"]["domain_objects"])


def declared_rejection(data, code):
    """Construct an expected legal refusal without calling diagnostic/transition code."""
    data = copy.deepcopy(data); before, out = data["before"], data["after"]["execution_outcome"]
    data["after"]["domain_objects"] = copy.deepcopy(before["domain_objects"])
    out["outcome"] = "REJECTED"
    ids = {k: out["command_result"][k] for k in ["command_id", "attempt_id", "generation"]}
    out["command_rejection"] = {**ids, "rejection_error_code": code, "diagnostic_codes": [code], "blocking_protective_order_ids": []}
    retry = code == "ERR_MANDATE_SOURCE_UNAVAILABLE"
    out["command_result"].update(status="REJECTED", applied_versions=[], retry_disposition="NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY" if retry else "FINAL")
    out["command_record"]["status"] = "IN_FLIGHT" if retry else "REJECTED"
    out["command_attempt"]["status"] = "FAILED_RETRYABLE" if retry else "REJECTED"
    out["audit_event"].update(outcome="REJECTED", written_versions=[])
    refresh_bindings(data)
    return data


class BoundaryTests(unittest.TestCase):
    def assert_valid(self, data):
        result = b.validate_transition(data)
        self.assertTrue(result["is_valid"], result)

    def assert_code(self, data, code, layer="OFFLINE_SEMANTIC"):
        result = b.validate_transition(data)
        self.assertEqual((result["validation_layer"], result["error_code"]), (layer, code), result)

    def test_full_chain_counts_only_independent_verified_increments(self):
        names = ["pt_approve_all_phases", "pt_authorize_phase1", "pt_record_handoff", "pt_log_receipt", "pt_verify_partial", "pt_log_second_receipt", "pt_verify_phase1_complete", "pt_authorize_phase2", "pt_record_phase2", "pt_log_phase2", "pt_commitment_completed"]
        expected = [0, 0, 0, 0, 2500, 2500, 4000, 4000, 4000, 4000, 10000]
        previous = None
        for name, amount in zip(names, expected):
            data = fixture(name)
            with self.subTest(name=name):
                self.assert_valid(data)
                if previous is not None: self.assertEqual(data["before"]["domain_objects"], previous)
                after = data["after"]["domain_objects"]
                self.assertEqual(after["commitments"][0]["verified_fulfilled_minor"], amount)
                self.assertEqual(after["resource_pools"][0]["committed_obligations_minor"], 10000)
                previous = after
        self.assertEqual(previous["commitments"][0]["commitment_status"], "COMPLETED")
        self.assertEqual([t["ticket_status"] for t in previous["tickets"]], ["EXTERNAL_RECEIPT_LOGGED"] * 2)

    def test_cancel_changes_only_ticket_and_derived_reservation(self):
        data = fixture("pt_cancel_queued"); before, after = data["before"]["domain_objects"], data["after"]["domain_objects"]
        self.assertEqual(before["commitments"], after["commitments"])
        self.assertEqual(before["resource_pools"], after["resource_pools"])
        self.assertEqual(b.internal_ticket_reserved_minor(before, "commitment-1"), 10000)
        self.assertEqual(b.internal_ticket_reserved_minor(after, "commitment-1"), 6000)
        data = fixture("pt_reauthorize_expired")
        self.assertEqual(data["after"]["domain_objects"]["tickets"][0]["handoff_authorization_history"], [data["before"]["domain_objects"]["tickets"][0]["handoff_authorization"]])
        self.assertEqual(data["before"]["domain_objects"]["effectiveness_envelopes"], data["after"]["domain_objects"]["effectiveness_envelopes"][:-1])

    def test_2a_and_2b_share_time_scope_and_multi_order_truth_table(self):
        base = fixture("pt_authorize_phase1")["before"]["domain_objects"]
        for name in ["pt_issue", "pt_second_order", "pt_lift_one_of_two", "pt_extend_new_order", "pt_late_extension"]:
            old = b.load_json(ROOT / "fixtures/aw001_slice2/2a" / (name + ".json"))["after"]["domain_objects"]
            for scope in ["DIRECT_ONLY", "INCLUDE_DESCENDANTS"]:
                source = copy.deepcopy(old)
                for order in source["protective_orders"]:
                    order["scope_mode"] = scope
                    env = next(e for e in source["effectiveness_envelopes"] if e["envelope_id"] == order["issuance_envelope_id"])
                    env["order_spec_sha256"] = b.digest({k: order[k] for k in a.ORDER_FIELDS})
                current = copy.deepcopy(base)
                current["protective_orders"] = source["protective_orders"]
                current["protection_envelopes"] = source["effectiveness_envelopes"]
                # Match 2B's required commitment parent for every ticket in both inputs.
                source["resources"][3]["parent_object_id"] = "commitment-1"
                for target in ["case-1", "commitment-1", "ticket-1", "ticket-2"]:
                    for now in ["2026-11-05T03:59:59Z", "2026-11-05T04:00:00Z", "2026-11-08T03:59:59Z", "2026-11-08T04:00:00Z", "2026-11-09T04:00:00Z"]:
                        with self.subTest(name=name, scope=scope, target=target, now=now):
                            self.assertEqual(b.evaluate_protective_orders(current, target, b.AUTHORIZE, now), a.evaluate_protective_orders(source, target, b.AUTHORIZE, now))

    def test_new_log_power_is_checked_and_unknown_power_is_not_ignored(self):
        data = fixture("pt_rejected_new_po_before_handoff")["before"]["domain_objects"]
        order = data["protective_orders"][0]; order["blocked_powers"] = [b.LOG]
        data["protection_envelopes"][0]["order_spec_sha256"] = b.digest({k: order[k] for k in a.ORDER_FIELDS})
        self.assertEqual(b.evaluate_protective_orders(data, "ticket-1", b.LOG, "2026-11-06T04:00:00Z"), ["po-1"])
        with self.assertRaises(b.ContractFailure) as error:
            b.evaluate_protective_orders(data, "ticket-1", "BYPASS", "2026-11-06T04:00:00Z")
        self.assertEqual(error.exception.report["error_code"], "ERR_INVALID_GATE_ARGUMENT")

    def test_malformed_parent_and_cross_tenant_fail_closed(self):
        for field, value in [("parent_object_id", "missing"), ("parent_object_id", "ticket-1"), ("tenant_node_id", "other"), ("case_id", "other")]:
            data = fixture("pt_authorize_phase1")["before"]["domain_objects"]
            data["tickets"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(b.ContractFailure):
                b.evaluate_protective_orders(data, "ticket-1", b.AUTHORIZE, "2026-11-06T04:00:00Z")

    def test_expiry_boundaries_and_origin_unavailable(self):
        data = fixture("pt_rejected_handoff_permission_expired")
        data["server_context"]["evaluated_at"] = "2026-11-20T00:00:00Z"
        for receipt in [data["server_context"]["generated_auth_receipt"], data["after"]["execution_outcome"]["auth_check_receipt"]]: receipt["checked_at"] = "2026-11-20T00:00:00Z"
        data["after"]["execution_outcome"]["audit_event"]["checked_at"] = "2026-11-20T00:00:00Z"
        refresh_bindings(data); self.assert_valid(data)
        data = fixture("pt_record_handoff")
        data["server_context"]["origin_authority_source_status"] = "UNAVAILABLE"
        self.assert_valid(declared_rejection(data, "ERR_MANDATE_SOURCE_UNAVAILABLE"))

    def test_zero_delta_verdicts_preserve_parent_and_do_not_hold(self):
        for name in ["pt_verify_not_fulfilled", "pt_verify_insufficient"]:
            data = fixture(name); before, after = data["before"]["domain_objects"], data["after"]["domain_objects"]
            self.assert_valid(data)
            self.assertEqual(before["commitments"], after["commitments"])
            self.assertEqual(before["protective_orders"], after["protective_orders"])
            self.assertEqual(after["execution_verifications"][-1]["verified_delta_minor"], 0)

    def test_verified_amount_cannot_exceed_phase_even_across_receipts(self):
        data = fixture("pt_verify_phase1_complete")
        data["command"]["payload"]["verified_delta_minor"] = 1501
        self.assert_valid(declared_rejection(data, "ERR_VERIFIED_AMOUNT_EXCEEDED"))
        for name in ["pt_verify_not_fulfilled", "pt_verify_insufficient"]:
            data = fixture(name); data["command"]["payload"]["verified_delta_minor"] = 1
            self.assert_valid(declared_rejection(data, "ERR_VERIFIED_AMOUNT_EXCEEDED"))

    def test_dependent_mandate_and_metric_bindings(self):
        variants = [("authority_snapshots", "powers", [b.HANDOFF], "ERR_HANDOFF_DEPENDENCY_INVALIDATED"),
                    ("authority_snapshots", "principal_id", "other", "ERR_HANDOFF_DEPENDENCY_INVALIDATED"),
                    ("authority_snapshots", "allowed_target_ids", ["ticket-2"], "ERR_HANDOFF_DEPENDENCY_INVALIDATED"),
                    ("authority_snapshots", "valid_until", "2026-11-06T04:00:00Z", "ERR_HANDOFF_DEPENDENCY_INVALIDATED"),
                    ("metric_evaluations", "commitment_id", "other", "ERR_METRIC_COMPLIANCE_UNMET"),
                    ("metric_evaluations", "phase_seq", 2, "ERR_METRIC_COMPLIANCE_UNMET")]
        for collection, key, value, code in variants:
            data = fixture("pt_record_handoff"); data["server_context"][collection][0][key] = value
            with self.subTest(collection=collection, key=key): self.assert_valid(declared_rejection(data, code))

    def test_required_responsible_member_unknown_and_optional_template_rule(self):
        data = fixture("pt_verify_partial")
        data["before"]["domain_objects"]["tickets"][0]["handoff_record"]["operator_member_id"] = None
        self.assert_valid(declared_rejection(data, "ERR_SOD_IDENTITY_UNMAPPED"))
        self.assert_valid(fixture("pt_verify_executor_rule_disabled"))
        self.assert_valid(fixture("pt_rejected_template_executor"))
        self.assert_valid(fixture("pt_rejected_verifier_is_finance"))
        self.assert_valid(fixture("pt_rejected_verifier_is_operator"))

    def test_refusals_do_not_mutate_domain_or_claim_success(self):
        for name in POSITIVE:
            if not name.startswith("pt_rejected_"): continue
            data = fixture(name); original = copy.deepcopy(data)
            with self.subTest(name=name):
                self.assert_valid(data)
                self.assertEqual(data["before"]["domain_objects"], data["after"]["domain_objects"])
                self.assertEqual(data["after"]["execution_outcome"]["outcome"], "REJECTED")
                self.assertEqual(data, original)
        mixed = fixture("pt_rejected_source_and_hold")["after"]["execution_outcome"]
        self.assertEqual(mixed["command_result"]["retry_disposition"], "FINAL")
        single = fixture("pt_rejected_source_unavailable")["after"]["execution_outcome"]
        self.assertEqual(single["command_attempt"]["status"], "FAILED_RETRYABLE")

    def test_result_unknown_does_not_claim_rollback(self):
        data = fixture("pt_commit_unknown"); self.assert_valid(data)
        self.assertIsNone(data["after"]["domain_objects"])
        out = data["after"]["execution_outcome"]
        for key in ["command_record", "command_result", "audit_event", "auth_check_receipt"]: self.assertIsNone(out[key])
        self.assertEqual(out["fault_observation"]["persistence_claim"], "NOT_ASSERTED")
        rollback = fixture("pt_confirmed_rollback"); self.assert_valid(rollback)
        self.assertEqual(rollback["before"]["domain_objects"], rollback["after"]["domain_objects"])

    def test_closed_inputs_strict_json_and_invalid_dates(self):
        for path in [["command"], ["command", "payload"], ["command", "mandate_claim"], ["command", "payload", "effectiveness_envelope_input"], ["command", "payload", "new_commitment"]]:
            data = fixture("pt_approve_all_phases"); node = data
            for key in path: node = node[key]
            node["auth_check_receipt"] = "forged"
            with self.subTest(path=path): self.assert_code(data, "ERR_CLIENT_FORBIDDEN_FIELD", "JSON_SCHEMA")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for text in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}']:
                path.write_text(text); self.assertEqual(b.validate_file(path)["error_code"], "ERR_INVALID_JSON")
        for date in ["2026-02-30T00:00:00Z", "2026-11-06T04:00:00+00:00"]:
            data = fixture("pt_approve_all_phases"); data["server_context"]["evaluated_at"] = date
            self.assertEqual(b.validate_transition(data)["validation_layer"], "JSON_SCHEMA")

    def test_registry_cannot_disable_money_or_sod_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            for validator in ["CHECK_RESOURCE_LEDGER", "CHECK_SOD_AND_DEPENDENCIES", "CHECK_FOUR_TUPLE_COMPLETE"]:
                data = b.load_json(b.DEFAULT_REGISTRY); data["actions"][b.VERIFY]["required_validators"].remove(validator)
                path.write_text(json.dumps(data)); self.assertEqual(b.check_registry(path)["error_code"], "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING")

    def test_cli_valid_refusal_and_invalid_record(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(ROOT / "tools/validate_aw001_slice2b.py")]
            r = subprocess.run(command + [str(FIXTURES / "pt_rejected_origin_revoked.json")], cwd=directory, text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertTrue(json.loads(r.stdout)["is_valid"])
            r = subprocess.run(command + [str(FIXTURES / "ft_missing_remedy.json"), str(FIXTURES / "pt_commit_unknown.json")], cwd=directory, text=True, capture_output=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual([json.loads(line)["is_valid"] for line in r.stdout.splitlines()], [False, True])

    def test_unmapped_sentinel_is_not_a_verified_member(self):
        data = fixture("pt_verify_partial")
        data["server_context"]["authenticated_member_id"] = "UNMAPPED"
        data["server_context"]["member_mappings"][0]["member_id"] = "UNMAPPED"
        self.assert_valid(declared_rejection(data, "ERR_SOD_IDENTITY_UNMAPPED"))

    def test_unmapped_responsible_member_cannot_pass_sod(self):
        data = fixture("pt_verify_partial")
        data["before"]["domain_objects"]["tickets"][0]["handoff_authorization"]["authorizer_member_id"] = "UNMAPPED"
        self.assert_valid(declared_rejection(data, "ERR_SOD_IDENTITY_UNMAPPED"))

    def test_negative_fixtures_isolate_one_declared_path(self):
        def differences(left, right, path=()):
            if isinstance(left, dict) and isinstance(right, dict):
                result = []
                for key in sorted(left.keys() | right.keys()):
                    if key not in left or key not in right: result.append(path + (key,))
                    else: result.extend(differences(left[key], right[key], path + (key,)))
                return result
            if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
                return [d for i, (a, b) in enumerate(zip(left, right)) for d in differences(a, b, path + (i,))]
            return [] if left == right else [path]
        mutations = {
            'ft_client_identity': ('pt_approve_all_phases', ('command', 'authenticated_member_id')),
            'ft_collective_approval': ('pt_approve_all_phases', ('command', 'payload', 'approver_member_ids')),
            'ft_missing_remedy': ('pt_approve_all_phases', ('command', 'payload', 'effectiveness_envelope_input', 'remedy_spec')),
            'ft_empty_evidence': ('pt_approve_all_phases', ('command', 'payload', 'effectiveness_envelope_input', 'evidence_refs')),
            'ft_unknown_action': ('pt_approve_all_phases', ('command', 'requested_action')),
            'ft_skip_check': ('pt_approve_all_phases', ('command', 'payload', 'skip_four_tuple_check')),
            'ft_cancel_reduces_commitment': ('pt_cancel_queued', ('after', 'domain_objects', 'commitments', 0, 'total_obligated_minor')),
            'ft_cancel_releases_pool': ('pt_cancel_queued', ('after', 'domain_objects', 'resource_pools', 0, 'committed_obligations_minor')),
            'ft_approval_omits_second_ticket': ('pt_approve_all_phases', ('after', 'domain_objects', 'tickets')),
            'ft_authorize_claims_handoff': ('pt_authorize_phase1', ('after', 'domain_objects', 'tickets', 0, 'ticket_status')),
            'ft_reauthorize_rewrites_history': ('pt_reauthorize_expired', ('after', 'domain_objects', 'tickets', 0, 'handoff_authorization_history', 0, 'handoff_expires_at')),
            'ft_log_counts_fulfilled': ('pt_log_receipt', ('after', 'domain_objects', 'commitments', 0, 'verified_fulfilled_minor')),
            'ft_verify_adds_verified_state': ('pt_verify_partial', ('after', 'domain_objects', 'tickets', 0, 'ticket_status')),
            'ft_partial_completes_commitment': ('pt_verify_partial', ('after', 'domain_objects', 'commitments', 0, 'commitment_status')),
            'ft_zero_verdict_changes_commitment_version': ('pt_verify_insufficient', ('after', 'domain_objects', 'commitments', 0, 'version_seq')),
            'ft_rejection_mutates_blocked': ('pt_rejected_authorize_parent_po', ('after', 'domain_objects', 'tickets', 0, 'ticket_status')),
            'ft_rejection_wrong_code': ('pt_rejected_origin_revoked', ('after', 'execution_outcome', 'command_rejection', 'rejection_error_code')),
            'ft_target_version': ('pt_authorize_phase1', ('command', 'target_expected_version_seq')),
            'ft_dependency_version': ('pt_verify_partial', ('command', 'dependency_versions', 1, 'version_seq')),
            'ft_receipt_attempt': ('pt_approve_all_phases', ('server_context', 'generated_auth_receipt', 'attempt_id')),
            'ft_context_digest': ('pt_approve_all_phases', ('server_context', 'generated_auth_receipt', 'context_sha256')),
            'ft_attempt_generation': ('pt_approve_all_phases', ('before', 'command_attempt', 'generation')),
            'ft_result_generation': ('pt_approve_all_phases', ('after', 'execution_outcome', 'command_result', 'generation')),
            'ft_audit_digest': ('pt_approve_all_phases', ('after', 'execution_outcome', 'audit_event', 'after_domain_sha256')),
            'ft_retry_terminal': ('pt_rejected_source_unavailable', ('after', 'execution_outcome', 'command_result', 'retry_disposition')),
            'ft_unknown_claims_rollback': ('pt_commit_unknown', ('after', 'domain_objects')),
            'ft_money_fraction': ('pt_approve_all_phases', ('command', 'payload', 'new_commitment', 'total_obligated_minor')),
            'ft_source_claims_verified': ('pt_rejected_source_unavailable', ('server_context', 'generated_auth_receipt', 'verification_status')),
        }
        for name, (base, path) in mutations.items():
            with self.subTest(name=name): self.assertEqual(differences(fixture(base), fixture(name)), [path])

    def test_each_refusal_rejects_business_mutation_and_false_success(self):
        for name in POSITIVE:
            if not name.startswith("pt_rejected_"): continue
            with self.subTest(name=name):
                data = fixture(name)
                data["after"]["domain_objects"]["cases"][0]["version_seq"] += 1
                self.assert_code(data, "ERR_REJECTED_DOMAIN_MUTATION")
                data = fixture(name); data["after"]["execution_outcome"]["outcome"] = "APPLIED"
                self.assertFalse(b.validate_transition(data)["is_valid"])

    def test_ledger_and_units_cannot_be_redefined_by_summary(self):
        for collection, field, value, code in [
            ("resource_pools", "committed_obligations_minor", 0, "ERR_RESOURCE_LEDGER_MISMATCH"),
            ("commitments", "verified_fulfilled_minor", 1, "ERR_RESOURCE_LEDGER_MISMATCH"),
            ("tickets", "cumulative_verified_minor", 1, "ERR_RESOURCE_LEDGER_MISMATCH"),
            ("commitments", "resource_spec", {"resource_kind": "COMPUTE_TIME", "unit_code": "GPU_MINUTES"}, "ERR_RESOURCE_SCOPE_MISMATCH"),
        ]:
            data = fixture("pt_authorize_phase1")
            data["before"]["domain_objects"][collection][0][field] = value
            with self.subTest(collection=collection, field=field): self.assert_code(data, code)
        data = fixture("pt_approve_all_phases")
        data["command"]["payload"]["new_commitment"]["resource_spec"]["unit_code"] = "GPU_MINUTES"
        self.assertEqual(b.validate_transition(data)["validation_layer"], "JSON_SCHEMA")

    def test_scope_and_authority_amount_caps_apply(self):
        for location in ["scope", "mandate"]:
            data = fixture("pt_approve_all_phases")
            if location == "scope":
                data["command"]["payload"]["effectiveness_envelope_input"]["scope_spec"]["max_amount_minor"] = 9999
                code = "ERR_EFFECTIVENESS_SCOPE_MISMATCH"
            else:
                data["server_context"]["authority_snapshot"]["max_amount_minor"] = 9999
                code = "ERR_MANDATE_SCOPE_MISMATCH"
            self.assert_valid(declared_rejection(data, code))
        data = fixture("pt_authorize_phase1")
        data["command"]["payload"]["handoff_expires_at"] = "2026-12-02T00:00:00Z"
        self.assert_valid(declared_rejection(data, "ERR_HANDOFF_AUTHORIZATION_EXPIRED"))

    def test_receipt_version_and_nonexistent_receipt_fail(self):
        for key, value in [("target_receipt_logged_version_seq", 3), ("target_receipt_id", "not-found")]:
            data = fixture("pt_verify_partial"); data["command"]["payload"][key] = value
            self.assert_valid(declared_rejection(data, "ERR_RECEIPT_REFERENCE_MISMATCH"))

    def test_incorrect_state_cannot_skip_handoff_or_cancelled_ticket(self):
        data = fixture("pt_log_receipt")
        ready = fixture("pt_record_handoff")["before"]["domain_objects"]
        data["before"]["domain_objects"] = copy.deepcopy(ready)
        data["command"]["target_expected_version_seq"] = 2
        data["server_context"]["generated_auth_receipt"]["evaluated_target_version_seq"] = 2
        self.assert_valid(declared_rejection(data, "ERR_INVALID_TICKET_STATE_TRANSITION"))
        data = fixture("pt_authorize_phase1")
        data["before"]["domain_objects"] = fixture("pt_cancel_queued")["after"]["domain_objects"]
        data["command"]["target_expected_version_seq"] = 2
        data["server_context"]["generated_auth_receipt"]["evaluated_target_version_seq"] = 2
        self.assert_valid(declared_rejection(data, "ERR_INVALID_TICKET_STATE_TRANSITION"))

    def test_pool_reference_cannot_point_to_a_case_object(self):
        data = fixture("pt_approve_all_phases")
        data["command"]["payload"]["new_commitment"]["budget_pool_id"] = "case-1"
        data["command"]["dependency_versions"] = [{"object_id": "case-1", "version_seq": 1}]
        refresh_bindings(data)
        self.assert_code(data, "ERR_INVALID_RESOURCE_POOL_REFERENCE")

    def test_fixture_inventory_matches_registered_assertions(self):
        self.assertEqual({p.stem for p in FIXTURES.glob("*.json")}, set(CASES))


if __name__ == "__main__":
    unittest.main()
