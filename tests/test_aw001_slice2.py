"""Slice 2A synthetic contracts: no online authority, locks or actual side effects."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.validate_aw001_slice2 import (
    DEFAULT_REGISTRY, ROOT, SCHEMA, VALIDATOR, ORDER_FIELDS, ContractFailure,
    check_registry, digest, evaluate_protective_orders, load_json, validate_file,
    validate_transition,
)

FIXTURES = ROOT / "fixtures/aw001_slice2/2a"


def fixture(name):
    return load_json(FIXTURES / (name + ".json"))


# Expectations belong to the tests, never to client input or the validator.
CASES = {
    **{name: ("OFFLINE_SEMANTIC", None) for name in [
        "pt_issue", "pt_second_order", "pt_lift_one_of_two", "pt_extend_new_order",
        "pt_late_extension", "pt_rejected_revoked", "pt_rejected_expired",
        "pt_rejected_source_unavailable", "pt_rejected_wrong_power",
        "pt_rejected_self_lift", "pt_rejected_unmapped_member", "pt_rejected_expired_lift",
        "pt_rejected_active_hold", "pt_rejected_multiple_causes",
        "pt_confirmed_rollback", "pt_commit_unknown",
    ]},
    "ft_client_identity": ("JSON_SCHEMA", "ERR_CLIENT_FORBIDDEN_FIELD"),
    "ft_missing_remedy": ("JSON_SCHEMA", "ERR_INCOMPLETE_FOUR_TUPLE"),
    "ft_empty_evidence": ("JSON_SCHEMA", "ERR_INCOMPLETE_FOUR_TUPLE"),
    "ft_unknown_action": ("JSON_SCHEMA", "ERR_UNREGISTERED_ACTION_ID"),
    "ft_skip_validator": ("JSON_SCHEMA", "ERR_CLIENT_FORBIDDEN_FIELD"),
    "ft_expected_version": ("OFFLINE_SEMANTIC", "ERR_VERSION_SEQ_MISMATCH"),
    "ft_attempt_generation": ("OFFLINE_SEMANTIC", "ERR_ATTEMPT_GENERATION_MISMATCH"),
    "ft_receipt_attempt": ("OFFLINE_SEMANTIC", "ERR_RECEIPT_BINDING_MISMATCH"),
    "ft_receipt_forged_active": ("OFFLINE_SEMANTIC", "ERR_RECEIPT_BINDING_MISMATCH"),
    "ft_rejection_mutates_ticket": ("OFFLINE_SEMANTIC", "ERR_REJECTED_DOMAIN_MUTATION"),
    "ft_untouched_resource_version": ("OFFLINE_SEMANTIC", "ERR_APPLIED_WRITE_SET_MISMATCH"),
    "ft_extension_overwrites_expiry": ("OFFLINE_SEMANTIC", "ERR_APPLIED_WRITE_SET_MISMATCH"),
    "ft_extension_rewrites_history": ("OFFLINE_SEMANTIC", "ERR_APPLIED_WRITE_SET_MISMATCH"),
    "ft_audit_digest": ("OFFLINE_SEMANTIC", "ERR_AUDIT_BINDING_MISMATCH"),
    "ft_result_generation": ("OFFLINE_SEMANTIC", "ERR_COMMAND_RESULT_MISMATCH"),
    "ft_retry_made_terminal": ("OFFLINE_SEMANTIC", "ERR_COMMAND_RESULT_MISMATCH"),
    "ft_unknown_claims_rollback": ("JSON_SCHEMA", "ERR_SCHEMA_VALIDATION"),
    "ft_fault_claims_persisted": ("JSON_SCHEMA", "ERR_SCHEMA_VALIDATION"),
    "ft_terminal_reexecution": ("OFFLINE_SEMANTIC", "ERR_TERMINAL_COMMAND_REEXECUTION"),
    "ft_revoked_claims_applied": ("OFFLINE_SEMANTIC", "ERR_DECLARED_OUTCOME_MISMATCH"),
    "registry_valid": ("REGISTRY_LOADER", None),
    "registry_missing_validator": ("REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING"),
    "registry_lock_order": ("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH"),
}


class GoldenTransitionTests(unittest.TestCase):
    pass


def golden_test(name):
    def test(self):
        layer, code = CASES[name]
        path = FIXTURES / (name + ".json")
        result = check_registry(path) if layer == "REGISTRY_LOADER" else validate_file(path)
        self.assertEqual((result["is_valid"], result["validation_layer"], result["error_code"]), (code is None, layer, code), result)
        if code:
            self.assertTrue(result["failed_path"].startswith("/"))
        else:
            self.assertIsNone(result["failed_path"])
    return test


for name in CASES:
    setattr(GoldenTransitionTests, "test_" + name, golden_test(name))


def domain(name="pt_issue", side="after"):
    return fixture(name)[side]["domain_objects"]


def reset_order_digest(data, order):
    """Update a synthetic historical source after an intentional scope mutation."""
    env = next(e for e in data["effectiveness_envelopes"] if e["envelope_id"] == order["issuance_envelope_id"])
    env["order_spec_sha256"] = digest({key: order[key] for key in ORDER_FIELDS})


class BoundaryTests(unittest.TestCase):
    def assert_code(self, data, code, layer="OFFLINE_SEMANTIC"):
        result = validate_transition(data)
        self.assertEqual((result["validation_layer"], result["error_code"]), (layer, code), result)

    def gate(self, data, target="ticket-1", power="AUTHORIZE_TICKET_HANDOFF", now="2026-11-05T04:00:00Z"):
        return evaluate_protective_orders(data, target, power, now)

    def test_half_open_time_and_expired_active_label(self):
        data = domain()
        for now, expected in [
            ("2026-11-05T03:59:59Z", []), ("2026-11-05T04:00:00Z", ["po-1"]),
            ("2026-11-08T03:59:59Z", ["po-1"]), ("2026-11-08T04:00:00Z", []),
            ("2026-11-09T04:00:00Z", []),
        ]:
            with self.subTest(now=now): self.assertEqual(self.gate(data, now=now), expected)
        self.assertEqual(data["protective_orders"][0]["order_status"], "ACTIVE_HOLD")

    def test_multiple_orders_and_unrelated_target_or_power(self):
        before = domain("pt_lift_one_of_two", "before")
        self.assertEqual(self.gate(before), ["po-1", "po-2"])
        after = domain("pt_lift_one_of_two")
        self.assertEqual(self.gate(after), ["po-1"])
        self.assertEqual(self.gate(after, target="ticket-2"), [])
        self.assertEqual(self.gate(after, power="CANCEL_INTERNAL_TICKET"), [])
        self.assertEqual(before["resources"], after["resources"])

    def test_direct_and_descendant_scope(self):
        data = domain(); order = data["protective_orders"][0]
        order["scope_mode"] = "DIRECT_ONLY"; reset_order_digest(data, order)
        self.assertEqual(self.gate(data), [])
        self.assertEqual(self.gate(data, target="commitment-1"), ["po-1"])
        order["protected_target_object_ids"] = ["case-1"]
        order["scope_mode"] = "INCLUDE_DESCENDANTS"; reset_order_digest(data, order)
        self.assertEqual(self.gate(data, target="ticket-2"), ["po-1"])

    def test_foreign_case_and_tenant_are_not_covered(self):
        data = domain(); source = copy.deepcopy(data)
        # Construct a second consistent historical case, not an invalid cross-tenant edge.
        encoded = json.dumps(source).replace('node-1', 'node-2').replace('case-1', 'case-2')
        encoded = encoded.replace('commitment-1', 'commitment-2').replace('ticket-1', 'ticket-3').replace('ticket-2', 'ticket-4').replace('po-1', 'po-foreign').replace('env-issue', 'env-foreign')
        foreign = json.loads(encoded)
        reset_order_digest(foreign, foreign["protective_orders"][0])
        for key in data: data[key].extend(foreign[key])
        self.assertEqual(self.gate(data), ["po-1"])
        self.assertEqual(self.gate(data, target="ticket-3"), ["po-foreign"])

    def test_invalid_hierarchy_is_not_silently_treated_as_no_hold(self):
        for parent in ["missing", "ticket-1"]:
            data = domain(); data["resources"][2]["parent_object_id"] = parent
            with self.subTest(parent=parent), self.assertRaises(ContractFailure) as error:
                self.gate(data)
            self.assertEqual(error.exception.report["error_code"], "ERR_INVALID_OBJECT_HIERARCHY")

    def test_extension_appends_and_gap_has_no_retroactive_effect(self):
        data = fixture("pt_extend_new_order")
        self.assertTrue(validate_transition(data)["is_valid"])
        self.assertEqual(data["before"]["domain_objects"]["effectiveness_envelopes"][0], data["after"]["domain_objects"]["effectiveness_envelopes"][0])
        orders = data["after"]["domain_objects"]["protective_orders"]
        self.assertEqual([(o["order_id"], o["version_seq"]) for o in orders], [("po-1", 2), ("po-3", 1)])
        self.assertEqual(orders[0]["expires_at"], "2026-11-08T04:00:00Z")
        late = domain("pt_late_extension")
        self.assertEqual(self.gate(late, now="2026-11-08T12:00:00Z"), [])
        self.assertEqual(self.gate(late, now="2026-11-09T04:00:00Z"), ["po-late"])

    def test_legitimate_rejections_do_not_mutate_business(self):
        for name in CASES:
            if not name.startswith("pt_rejected_"): continue
            with self.subTest(name=name):
                data = fixture(name); original = copy.deepcopy(data)
                self.assertTrue(validate_transition(data)["is_valid"])
                self.assertEqual(data["after"]["domain_objects"], data["before"]["domain_objects"])
                self.assertEqual(data["after"]["execution_outcome"]["outcome"], "REJECTED")
                self.assertEqual(data, original)
        data = fixture("pt_rejected_source_unavailable")["after"]["execution_outcome"]
        self.assertEqual(data["command_record"]["status"], "IN_FLIGHT")
        self.assertEqual(data["command_attempt"]["status"], "FAILED_RETRYABLE")
        mixed = fixture("pt_rejected_multiple_causes")["after"]["execution_outcome"]
        self.assertEqual(mixed["command_result"]["retry_disposition"], "FINAL")
        self.assertEqual(mixed["command_rejection"]["rejection_error_code"], "ERR_BLOCKED_BY_PROTECTIVE_ORDER")

    def test_member_mapping_prevents_principal_alias_self_lift(self):
        data = fixture("pt_rejected_self_lift")
        self.assertEqual(data["server_context"]["authenticated_principal_id"], "principal-reviewer")
        self.assertEqual(data["server_context"]["authenticated_member_id"], "member-worker")
        self.assertTrue(validate_transition(data)["is_valid"])
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"]["rejection_error_code"], "ERR_SOD_EXECUTOR_CANNOT_LIFT_HOLD")
        self.assertTrue(validate_transition(fixture("pt_lift_one_of_two"))["is_valid"])

    def test_receipt_coordination_and_context_are_bound(self):
        for path, value, code in [
            (["before", "command_record", "request_sha256"], "sha256:" + "d" * 64, "ERR_COMMAND_BINDING_MISMATCH"),
            (["server_context", "generation"], 2, "ERR_COMMAND_BINDING_MISMATCH"),
            (["server_context", "generated_auth_receipt", "domain_sha256"], "sha256:" + "d" * 64, "ERR_RECEIPT_BINDING_MISMATCH"),
            (["server_context", "authority_snapshot", "principal_id"], "impostor", "ERR_RECEIPT_BINDING_MISMATCH"),
            (["server_context", "case_id"], "other-case", "ERR_CONTEXT_SCOPE_MISMATCH"),
            (["server_context", "evidence_snapshots", 0, "revision"], 2, "ERR_UNRESOLVED_EVIDENCE_REFERENCE"),
        ]:
            with self.subTest(path=path):
                data = fixture("pt_issue"); target = data
                for key in path[:-1]: target = target[key]
                target[path[-1]] = value
                self.assert_code(data, code)

    def test_unknown_and_confirmed_rollback_remain_distinct(self):
        unknown = fixture("pt_commit_unknown")["after"]
        rollback = fixture("pt_confirmed_rollback")
        self.assertIsNone(unknown["domain_objects"])
        self.assertIsNone(unknown["execution_outcome"]["command_record"])
        self.assertEqual(rollback["after"]["domain_objects"], rollback["before"]["domain_objects"])
        for name in ["pt_commit_unknown", "pt_confirmed_rollback"]:
            data = fixture(name)
            self.assertTrue(validate_transition(data)["is_valid"])
            for field in ["command_result", "command_rejection", "auth_check_receipt", "audit_event"]:
                self.assertIsNone(data["after"]["execution_outcome"][field])
            data["after"]["execution_outcome"]["audit_event"] = fixture("pt_issue")["after"]["execution_outcome"]["audit_event"]
            self.assertFalse(validate_transition(data)["is_valid"])

    def test_root_schema_closed_inputs_and_no_later_actions(self):
        self.assertEqual(SCHEMA["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(VALIDATOR.is_valid({}))
        for path in [["command"], ["command", "mandate_claim"], ["command", "payload"],
                     ["command", "payload", "new_order"], ["command", "payload", "effectiveness_envelope_input"],
                     ["command", "payload", "effectiveness_envelope_input", "scope_spec"],
                     ["command", "payload", "effectiveness_envelope_input", "remedy_spec"]]:
            data = fixture("pt_issue"); target = data
            for key in path: target = target[key]
            target["auth_check_receipt"] = "forged"
            with self.subTest(path=path): self.assert_code(data, "ERR_CLIENT_FORBIDDEN_FIELD", "JSON_SCHEMA")
        for action in ["AUTHORIZE_TICKET_HANDOFF", "ADJUDICATE_APPEAL"]:
            data = fixture("pt_issue"); data["command"]["requested_action"] = action
            self.assert_code(data, "ERR_UNREGISTERED_ACTION_ID", "JSON_SCHEMA")

    def test_registry_tampering_and_boolean_lock_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            for key, value, code in [
                ("requires_effectiveness_envelope", False, "ERR_ACTION_REGISTRY_VIOLATION"),
                ("required_power", "LIFT_PROTECTIVE_ORDER", "ERR_ACTION_REGISTRY_VIOLATION"),
                ("lock_levels", [False, True, 2, 3], "ERR_REGISTRY_LOCK_ORDER_MISMATCH"),
            ]:
                data = load_json(DEFAULT_REGISTRY); data["actions"]["ISSUE_PROTECTIVE_ORDER"][key] = value
                path.write_text(json.dumps(data))
                with self.subTest(key=key): self.assertEqual(check_registry(path)["error_code"], code)

    def test_strict_json_and_invalid_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for text in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}']:
                path.write_text(text); self.assertEqual(validate_file(path)["error_code"], "ERR_INVALID_JSON")
        for key, value in [("evaluated_at", "2026-02-30T04:00:00Z"), ("evaluated_at", "2026-11-05T04:00:00+00:00"), ("execution_enabled", True)]:
            data = fixture("pt_issue"); data["server_context"][key] = value
            self.assertEqual(validate_transition(data)["validation_layer"], "JSON_SCHEMA")

    def test_cli_reports_validation_not_business_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            base = [sys.executable, str(ROOT / "tools/validate_aw001_slice2.py")]
            run = subprocess.run(base + [str(FIXTURES / "pt_rejected_revoked.json")], cwd=directory, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(json.loads(run.stdout)["is_valid"])
            run = subprocess.run(base + [str(FIXTURES / "ft_missing_remedy.json"), str(FIXTURES / "pt_issue.json")], cwd=directory, text=True, capture_output=True)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertEqual([json.loads(line)["is_valid"] for line in run.stdout.splitlines()], [False, True])

    def test_order_cannot_outlive_its_effectiveness_scope(self):
        data = fixture("pt_issue")
        # Keep all derived bindings coherent; shorten only the proposed effect scope.
        scope = data["command"]["payload"]["effectiveness_envelope_input"]["scope_spec"]
        scope["effective_until"] = "2026-11-06T04:00:00Z"
        data["before"]["command_record"]["request_sha256"] = digest(data["command"])
        data["after"]["execution_outcome"]["command_record"]["request_sha256"] = digest(data["command"])
        data["after"]["domain_objects"]["effectiveness_envelopes"][0]["scope_spec"] = copy.deepcopy(scope)
        data["after"]["execution_outcome"]["audit_event"]["after_domain_sha256"] = digest(data["after"]["domain_objects"])
        self.assert_code(data, "ERR_DECLARED_OUTCOME_MISMATCH")

    def test_historical_order_requires_matching_scope_and_issuance_target(self):
        for field, value in [("case_id", "unrelated-case"), ("effective_until", "2026-11-06T04:00:00Z")]:
            data = domain(); data["effectiveness_envelopes"][0]["scope_spec"][field] = value
            with self.subTest(field=field), self.assertRaises(ContractFailure) as error:
                self.gate(data)
            self.assertEqual(error.exception.report["error_code"], "ERR_INVALID_ORDER_HISTORY")

    def test_negative_fixtures_isolate_declared_mutation(self):
        def differences(left, right, path=()):
            if isinstance(left, dict) and isinstance(right, dict):
                result = []
                for key in sorted(left.keys() | right.keys()):
                    if key not in left or key not in right: result.append(path + (key,))
                    else: result.extend(differences(left[key], right[key], path + (key,)))
                return result
            if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
                return [diff for i, (a, b) in enumerate(zip(left, right)) for diff in differences(a, b, path + (i,))]
            return [] if left == right else [path]
        changes = {
            'ft_client_identity': ('pt_issue', ('command', 'authenticated_principal_id')),
            'ft_missing_remedy': ('pt_issue', ('command', 'payload', 'effectiveness_envelope_input', 'remedy_spec')),
            'ft_empty_evidence': ('pt_issue', ('command', 'payload', 'effectiveness_envelope_input', 'evidence_refs')),
            'ft_unknown_action': ('pt_issue', ('command', 'requested_action')),
            'ft_skip_validator': ('pt_issue', ('command', 'payload', 'skip_four_tuple_check')),
            'ft_expected_version': ('pt_issue', ('command', 'target_expected_version_seq')),
            'ft_attempt_generation': ('pt_issue', ('before', 'command_attempt', 'generation')),
            'ft_receipt_attempt': ('pt_issue', ('server_context', 'generated_auth_receipt', 'attempt_id')),
            'ft_receipt_forged_active': ('pt_rejected_revoked', ('server_context', 'generated_auth_receipt', 'verification_status')),
            'ft_rejection_mutates_ticket': ('pt_rejected_revoked', ('after', 'domain_objects', 'resources', 2, 'recorded_status')),
            'ft_untouched_resource_version': ('pt_issue', ('after', 'domain_objects', 'resources', 0, 'version_seq')),
            'ft_extension_overwrites_expiry': ('pt_extend_new_order', ('after', 'domain_objects', 'protective_orders', 0, 'expires_at')),
            'ft_extension_rewrites_history': ('pt_extend_new_order', ('after', 'domain_objects', 'effectiveness_envelopes', 0, 'evaluated_target_version_seq')),
            'ft_audit_digest': ('pt_issue', ('after', 'execution_outcome', 'audit_event', 'after_domain_sha256')),
            'ft_result_generation': ('pt_issue', ('after', 'execution_outcome', 'command_result', 'generation')),
            'ft_retry_made_terminal': ('pt_rejected_source_unavailable', ('after', 'execution_outcome', 'command_result', 'retry_disposition')),
            'ft_unknown_claims_rollback': ('pt_commit_unknown', ('after', 'domain_objects')),
            'ft_fault_claims_persisted': ('pt_confirmed_rollback', ('after', 'execution_outcome', 'fault_observation', 'persistence_claim')),
            'ft_terminal_reexecution': ('pt_issue', ('before', 'command_record', 'status')),
        }
        for name, (base, path) in changes.items():
            with self.subTest(name=name):
                self.assertEqual(differences(fixture(base), fixture(name)), [path])

    def test_golden_inventory_matches_files(self):
        self.assertEqual({p.stem for p in FIXTURES.glob("*.json")}, set(CASES))


if __name__ == "__main__":
    unittest.main()
