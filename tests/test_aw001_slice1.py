"""Executable contract assertions; no real authentication or database execution."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.validate_aw001_slice1 import (
    DEFAULT_REGISTRY, ROOT, SCHEMA, check_registry, load_json,
    validate_file, validate_transition,
)
from jsonschema import Draft202012Validator


FIXTURES = ROOT / "fixtures/aw001_slice1"


def fixture(name):
    return load_json(FIXTURES / f"{name}.json")


# Expected answers are test code, never part of a command or a fixture.
CASES = {
    "PT-01a": ("pt01a", "OFFLINE_SEMANTIC", None),
    "PT-01b": ("pt01b", "OFFLINE_SEMANTIC", None),
    "FT-01a": ("ft01a", "JSON_SCHEMA", "ERR_CLIENT_FORBIDDEN_FIELD"),
    "FT-01b": ("ft01b", "JSON_SCHEMA", "ERR_INCOMPLETE_FOUR_TUPLE"),
    "FT-01c": ("ft01c", "JSON_SCHEMA", "ERR_INCOMPLETE_FOUR_TUPLE"),
    "FT-01d": ("ft01d", "OFFLINE_SEMANTIC", "ERR_RECEIPT_NOT_ACTIVE"),
    "FT-01e": ("ft01e", "OFFLINE_SEMANTIC", "ERR_VERSION_SEQ_MISMATCH"),
    "PT-02a": ("pt02a", "OFFLINE_SEMANTIC", None),
    "PT-02b": ("pt02b", "OFFLINE_SEMANTIC", None),
    "FT-02a": ("ft02a", "OFFLINE_SEMANTIC", "ERR_UNAUTHORIZED_POWER_FOR_ACTION"),
    "FT-02b": ("ft02b", "OFFLINE_SEMANTIC", "ERR_REJECTED_CLAIM_MARKED_EFFECTIVE"),
    "FT-02c": ("ft02c", "OFFLINE_SEMANTIC", "ERR_UNILATERAL_CLAIMANT_STANCE_MUTATION"),
    "FT-02d": ("ft02d", "OFFLINE_SEMANTIC", "ERR_NOT_CLAIMANT_PRINCIPAL"),
    "PT-16a": ("registry/pt16a", "REGISTRY_LOADER", None),
    "FT-16a": ("ft16a", "JSON_SCHEMA", "ERR_UNREGISTERED_ACTION_ID"),
    "FT-16b-1": ("ft16b-1", "JSON_SCHEMA", "ERR_CLIENT_FORBIDDEN_FIELD"),
    "FT-16b-2": ("registry/ft16b-2", "REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING"),
    "FT-02e": ("ft02e", "OFFLINE_SEMANTIC", "ERR_HISTORICAL_REVIEW_DECISION_MUTATION"),
    "FT-02f": ("ft02f", "OFFLINE_SEMANTIC", "ERR_EFFECTIVE_STANDING_MUTATION"),
    "FT-16b-3": ("registry/ft16b-3", "REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH"),
}


class GoldenTransitionTests(unittest.TestCase):
    """One unittest method per registered assertion, including expected rejection."""


def golden_test(assertion_id):
    def test(self):
        name, layer, code = CASES[assertion_id]
        path = FIXTURES / f"{name}.json"
        result = check_registry(path) if layer == "REGISTRY_LOADER" else validate_file(path)
        self.assertEqual(result["is_valid"], code is None, result)
        self.assertEqual(result["validation_layer"], layer, result)
        self.assertEqual(result["error_code"], code, result)
        if code is None:
            self.assertIsNone(result["failed_path"])
        else:
            self.assertTrue(result["failed_path"].startswith("/"), result)
    return test


for assertion_id in CASES:
    setattr(GoldenTransitionTests, "test_" + assertion_id.replace("-", "_"), golden_test(assertion_id))


class SliceBoundaryTests(unittest.TestCase):
    def assert_code(self, data, code, layer="OFFLINE_SEMANTIC"):
        result = validate_transition(data)
        self.assertEqual((result["validation_layer"], result["error_code"]), (layer, code), result)

    def test_negative_fixtures_change_only_the_declared_condition(self):
        def differences(left, right, path=()):
            if isinstance(left, dict) and isinstance(right, dict):
                found=[]
                for key in sorted(left.keys() | right.keys()):
                    if key not in left or key not in right:
                        found.append(path+(key,))
                    else:
                        found.extend(differences(left[key],right[key],path+(key,)))
                return found
            return [] if left == right else [path]
        mutations={
            "ft01a":("pt01b",[("command","authenticated_principal_id")]),
            "ft01b":("pt01b",[("command","payload","effectiveness_envelope_input","remedy_spec")]),
            "ft01c":("pt01b",[("command","payload","effectiveness_envelope_input","evidence_refs")]),
            "ft01d":("pt01b",[("server_context","generated_auth_receipt","verification_status")]),
            "ft01e":("pt01b",[("command","target_expected_version_seq")]),
            "ft02a":("pt02a",[("command","mandate_claim","claimed_power"),("server_context","generated_auth_receipt","requested_power")]),
            "ft02b":("pt02a",[("after","constraint_record","constraint_stage")]),
            "ft02c":("pt02a",[("after","constraint_record","claimant_post_review_stance","tradeoff_consent")]),
            "ft02d":("pt02b",[("server_context","authenticated_principal_id")]),
            "ft02e":("pt02b",[("after","review_decision_record","effectiveness_envelope","resulting_target_version_seq")]),
            "ft02f":("pt02b",[("after","constraint_record","effective_standing_status")]),
            "ft16a":("pt01b",[("command","requested_action")]),
            "ft16b-1":("pt01b",[("command","payload","skip_four_tuple_check")]),
            "registry/ft16b-2":("registry/pt16a",[("actions","CERTIFY_CASE_CONSTRAINT","required_validators")]),
            "registry/ft16b-3":("registry/pt16a",[("actions","CERTIFY_CASE_CONSTRAINT","lock_levels")]),
        }
        for name,(base,paths) in mutations.items():
            with self.subTest(fixture=name):
                self.assertEqual(differences(fixture(base),fixture(name)),sorted(paths))

    def test_root_schema_is_executable_and_closed(self):
        Draft202012Validator.check_schema(SCHEMA)
        validator = Draft202012Validator(SCHEMA)
        self.assertTrue(validator.is_valid(fixture("pt01b")))
        self.assertFalse(validator.is_valid({}))
        for path in [[], ["command"], ["command", "mandate_claim"],
                     ["command", "payload"], ["command", "payload", "effectiveness_envelope_input"],
                     ["command", "payload", "effectiveness_envelope_input", "scope_spec"],
                     ["command", "payload", "effectiveness_envelope_input", "remedy_spec"],
                     ["server_context"], ["after", "constraint_record"]]:
            with self.subTest(path=path):
                data=fixture("pt01b")
                target=data
                for key in path:
                    target=target[key]
                target["authenticated_principal_id" if path != ["server_context"] else "skip_check"]= "forged"
                self.assertFalse(validator.is_valid(data))
                self.assertFalse(validate_transition(data)["is_valid"])

    def test_requested_withdrawal_preserves_standing_and_history(self):
        for intent in ["REQUEST_SUBITEM_OPT_OUT", "REQUEST_CASE_WITHDRAWAL"]:
            with self.subTest(intent=intent):
                data=fixture("pt02b")
                data["command"]["payload"]["expressed_participation_intent"]=intent
                data["after"]["constraint_record"]["claimant_post_review_stance"]["expressed_participation_intent"]=intent
                original=copy.deepcopy(data)
                self.assertTrue(validate_transition(data)["is_valid"])
                self.assertEqual(data, original)
                self.assertEqual(data["before"]["review_decision_record"],data["after"]["review_decision_record"])

    def test_rejection_preserves_preexisting_stance_and_needs_remedy(self):
        data=fixture("pt02a")
        stance=fixture("pt02b")["after"]["constraint_record"]["claimant_post_review_stance"]
        data["before"]["constraint_record"]["claimant_post_review_stance"]=copy.deepcopy(stance)
        data["after"]["constraint_record"]["claimant_post_review_stance"]=copy.deepcopy(stance)
        self.assertTrue(validate_transition(data)["is_valid"])
        data["after"]["constraint_record"]["claimant_post_review_stance"]=fixture("pt02a")["before"]["constraint_record"]["claimant_post_review_stance"]
        self.assert_code(data,"ERR_UNILATERAL_CLAIMANT_STANCE_MUTATION")
        data=fixture("pt02a")
        del data["after"]["review_decision_record"]["effectiveness_envelope"]["remedy_spec"]
        self.assert_code(data,"ERR_INCOMPLETE_FOUR_TUPLE","JSON_SCHEMA")

    def test_receipt_and_authority_bindings_are_not_trusted_by_status(self):
        mutations=[
            (["server_context","generated_auth_receipt","command_id"],"wrong","ERR_RECEIPT_BINDING_MISMATCH"),
            (["server_context","generated_auth_receipt","attempt_id"],"wrong-attempt","ERR_RECEIPT_BINDING_MISMATCH"),
            (["server_context","generated_auth_receipt","evaluated_target_version_seq"],1,"ERR_VERSION_SEQ_MISMATCH"),
            (["server_context","authority_snapshot","principal_id"],"wrong","ERR_MANDATE_SNAPSHOT_MISMATCH"),
            (["server_context","authority_snapshot","version_hash"],"sha256:"+"c"*64,"ERR_MANDATE_SNAPSHOT_MISMATCH"),
            (["server_context","authority_snapshot","status"],"REVOKED","ERR_MANDATE_SNAPSHOT_MISMATCH"),
            (["server_context","authority_snapshot","valid_until"],"2026-11-05T04:00:00Z","ERR_MANDATE_SNAPSHOT_MISMATCH"),
            (["server_context","case_id"],"other-case","ERR_CONTEXT_SCOPE_MISMATCH"),
            (["server_context","acl_policy_refs"],["other-acl"],"ERR_CONTEXT_SCOPE_MISMATCH"),
            (["command","payload","effectiveness_envelope_input","scope_spec","case_id"],"other-case","ERR_SCOPE_MISMATCH"),
            (["server_context","evidence_snapshots",0,"revision"],2,"ERR_UNRESOLVED_EVIDENCE_REFERENCE"),
            (["after","constraint_record","raw_statement_text"],"rewritten","ERR_TRANSITION_MISMATCH"),
        ]
        for path,value,code in mutations:
            with self.subTest(path=path):
                data=fixture("pt01b");target=data
                for key in path[:-1]: target=target[key]
                target[path[-1]]=value
                if path[-1] == "attempt_id":
                    data["after"]["review_decision_record"]["effectiveness_envelope"]["auth_check_receipt"]["attempt_id"]=value
                self.assert_code(data,code)

    def test_no_stale_intake_or_fabricated_review_receipt_for_self_action(self):
        data=fixture("pt01a")
        data["server_context"]["intake_policy_check"]["result"]="DENIED"
        self.assert_code(data,"ERR_INTAKE_POLICY_DENIED")
        data=fixture("pt01a")
        data["server_context"]["intake_policy_check"]["target_object_id"]="other"
        self.assert_code(data,"ERR_INTAKE_POLICY_DENIED")
        data=fixture("pt02b")
        data["server_context"]["generated_auth_receipt"]=fixture("pt01b")["server_context"]["generated_auth_receipt"]
        self.assertFalse(validate_transition(data)["is_valid"])

    def test_quantitative_confirmation_needs_versioned_definition(self):
        data=fixture("pt01b")
        for state in ["before","after"]:
            data[state]["constraint_record"]["measurement_kind"]="QUANTITATIVE"
        self.assert_code(data,"ERR_METRIC_SPEC_REQUIRED")
        metric={"metric_id":"metric-01","version_seq":1,"definition_ref":data["server_context"]["evidence_snapshots"][0]}
        data["command"]["payload"]["metric_spec"]=copy.deepcopy(metric)
        data["after"]["constraint_record"]["metric_spec"]=copy.deepcopy(metric)
        self.assertTrue(validate_transition(data)["is_valid"])

    def test_registry_tampering_cannot_redefine_required_contract(self):
        registry=load_json(DEFAULT_REGISTRY)
        mutations=[("required_power","REJECT_SUBMISSION"), ("requires_effectiveness_envelope",False), ("verification_mechanism","INTAKE_POLICY_CHECK")]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"registry.json"
            for key,value in mutations:
                with self.subTest(key=key):
                    data=copy.deepcopy(registry)
                    data["actions"]["CERTIFY_CASE_CONSTRAINT"][key]=value
                    path.write_text(json.dumps(data))
                    result=check_registry(path)
                    self.assertEqual(result["validation_layer"],"REGISTRY_LOADER")
                    self.assertEqual(result["error_code"],"ERR_ACTION_REGISTRY_VIOLATION")
            path.write_text('{}')
            self.assertFalse(check_registry(path)["is_valid"])

    def test_strict_input_and_invalid_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bad.json"
            for text in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}']:
                with self.subTest(text=text):
                    path.write_text(text)
                    self.assertEqual(validate_file(path)["error_code"],"ERR_INVALID_JSON")
            self.assertEqual(validate_file(Path(directory)/"absent.json")["error_code"],"ERR_INPUT_UNREADABLE")
        for path,value in [(["server_context","checked_at"],"2026-02-30T04:00:00Z"),(["command","target_expected_version_seq"],0),(["server_context","execution_enabled"],True)]:
            data=fixture("pt01b");target=data
            for key in path[:-1]:target=target[key]
            target[path[-1]]=value
            self.assertEqual(validate_transition(data)["validation_layer"],"JSON_SCHEMA")

    def test_cli_reports_each_input_and_preserves_negative_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(ROOT/'tools/validate_aw001_slice1.py'),str(FIXTURES/'ft01b.json'),str(FIXTURES/'pt01a.json')],cwd=directory,text=True,capture_output=True)
            self.assertEqual(result.returncode,1,result.stderr)
            rows=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row['is_valid'] for row in rows],[False,True])
            self.assertEqual(rows[0]['error_code'],'ERR_INCOMPLETE_FOUR_TUPLE')
            self.assertNotIn('raw_statement_text',result.stdout)
            result=subprocess.run([sys.executable,str(ROOT/'tools/validate_aw001_slice1.py'),str(FIXTURES/'pt01a.json'),str(FIXTURES/'pt02b.json')],cwd=directory,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    unittest.main()
