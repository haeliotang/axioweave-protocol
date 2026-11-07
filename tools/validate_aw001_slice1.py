"""Offline AW-001 Slice 1 consistency checks; never authenticates or executes."""

import argparse
import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
# Reuse the existing strict reader and UTC format checker, including CLI outside cwd.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.validate_case_packet import FORMATS, load_json


SPEC = ROOT / "specs/aw001_slice1"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON_VALIDATOR = Draft202012Validator(
    {**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS,
)

SUBMIT = "SUBMIT_CONSTRAINT_CLAIM"
CERTIFY = "CERTIFY_CASE_CONSTRAINT"
REJECT = "REJECT_CONSTRAINT_CLAIM"
STANCE = "RECORD_CLAIMANT_STANCE"
POWERS = {SUBMIT: None, CERTIFY: "CERTIFY_CONSTRAINT", REJECT: "REJECT_SUBMISSION", STANCE: None}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder", "FulfillmentTicket"]
COMMON_CHECKS = ["CHECK_CONTEXT_AND_SCOPE", "CHECK_VERSION_TRANSITION"]
REVIEW_CHECKS = COMMON_CHECKS + ["CHECK_FOUR_TUPLE_COMPLETE", "CHECK_MANDATE_SNAPSHOT", "CHECK_RECEIPT_BINDING", "CHECK_CLAIMANT_STATE_PRESERVED"]
REQUIRED_CHECKS = {
    SUBMIT: COMMON_CHECKS + ["CHECK_INTAKE_POLICY"],
    CERTIFY: REVIEW_CHECKS,
    REJECT: REVIEW_CHECKS,
    STANCE: COMMON_CHECKS + ["CHECK_CLAIMANT_PRINCIPAL", "CHECK_HISTORICAL_DECISION_IMMUTABLE", "CHECK_EFFECTIVE_STANDING_PRESERVED"],
}
MECHANISMS = {SUBMIT: "INTAKE_POLICY_CHECK", CERTIFY: "MANDATE_CHECK", REJECT: "MANDATE_CHECK", STANCE: "CLAIMANT_SELF_ATTESTATION"}


def result(layer, code=None, path=None):
    return {"is_valid": code is None, "validation_layer": layer, "error_code": code, "failed_path": path}


class ContractFailure(Exception):
    def __init__(self, code, path, layer="OFFLINE_SEMANTIC"):
        self.result = result(layer, code, path)
        super().__init__(code)


def require(condition, code, path):
    if not condition:
        raise ContractFailure(code, path)


def pointer(parts):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def check_registry(path=DEFAULT_REGISTRY):
    """Test-only alternate paths do not affect the production validation entrypoint."""
    layer = "REGISTRY_LOADER"
    try:
        registry = load_json(path)
    except (OSError, ValueError):
        return result(layer, "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if (not isinstance(registry, dict)
            or set(registry) != {"contract_version", "global_lock_order", "actions"}
            or registry["contract_version"] != "0.2.0-rc1-slice1"
            or not isinstance(registry["actions"], dict)
            or set(registry["actions"]) != set(POWERS)):
        return result(layer, "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if registry["global_lock_order"] != LOCK_ORDER:
        return result(layer, "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/global_lock_order")
    for action, power in POWERS.items():
        entry = registry["actions"][action]
        prefix = "/actions/" + action
        fields = {"required_power", "verification_mechanism", "requires_effectiveness_envelope", "required_validators", "lock_levels"}
        if not isinstance(entry, dict) or set(entry) != fields:
            return result(layer, "ERR_ACTION_REGISTRY_VIOLATION", prefix)
        if (entry["required_power"] != power
                or entry["verification_mechanism"] != MECHANISMS[action]
                or entry["requires_effectiveness_envelope"] is not (power is not None)):
            return result(layer, "ERR_ACTION_REGISTRY_VIOLATION", prefix)
        checks = entry["required_validators"]
        if not isinstance(checks, list) or any(check not in checks for check in REQUIRED_CHECKS[action]):
            return result(layer, "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING", prefix + "/required_validators")
        if checks != REQUIRED_CHECKS[action]:
            return result(layer, "ERR_ACTION_REGISTRY_VIOLATION", prefix + "/required_validators")
        levels = entry["lock_levels"]
        expected = [0, 1, 2, 3] if power else [0, 2]
        if (not isinstance(levels, list) or any(type(level) is not int for level in levels)
                or levels != expected):
            return result(layer, "ERR_REGISTRY_LOCK_ORDER_MISMATCH", prefix + "/lock_levels")
    return result(layer)


def schema_result(error):
    path = list(error.absolute_path)
    code = "ERR_SCHEMA_VALIDATION"
    if path == ["command", "requested_action"] and error.validator == "enum":
        code = "ERR_UNREGISTERED_ACTION_ID"
    elif path[:1] == ["command"] and error.validator == "additionalProperties":
        code = "ERR_CLIENT_FORBIDDEN_FIELD"
    elif error.validator in {"required", "minItems"}:
        missing = set()
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = set(error.validator_value) - set(error.instance)
        if (any(part in {"effectiveness_envelope_input", "effectiveness_envelope"} for part in path)
                or missing & {"effectiveness_envelope_input", "effectiveness_envelope", "mandate_claim"}):
            code = "ERR_INCOMPLETE_FOUR_TUPLE"
    return result("JSON_SCHEMA", code, pointer(path))


def first_schema_error(validator, data):
    def leaves(error):
        if error.context:
            for child in error.context:
                yield from leaves(child)
        else:
            yield error
    errors = [child for error in validator.iter_errors(data) for child in leaves(error)]
    if not errors:
        return None
    # Keyword/path ordering is deterministic and independent of translated messages.
    def key(error):
        unknown_action = list(error.absolute_path) == ["command", "requested_action"] and error.validator == "enum"
        # Nullable records use anyOf; prefer the object branch's concrete error.
        null_branch = error.validator == "type" and error.validator_value == "null"
        return (not unknown_action, null_branch, pointer(error.absolute_path), str(error.validator), pointer(error.absolute_schema_path))
    return schema_result(min(errors, key=key))


def validate_prior_state(state):
    """Historical consistency only: do not re-authorize or rewrite an old decision."""
    record = state["constraint_record"]
    decision = state["review_decision_record"]
    stage = record["constraint_stage"]
    path = "/before"
    code = "ERR_INVALID_PRIOR_STATE"
    stance = record["claimant_post_review_stance"]
    if stance["stance_status"] == "UNSPECIFIED_PENDING_CLAIMANT":
        require(stance == {"stance_status": "UNSPECIFIED_PENDING_CLAIMANT", "tradeoff_consent": "UNSPECIFIED", "expressed_participation_intent": "CONTINUE_DELIBERATION_AND_APPEAL"}, code, path)
    else:
        require(stance["tradeoff_consent"] != "UNSPECIFIED", code, path)
    if stage != "DRAFT":
        require(bool(record.get("claim_title")) and bool(record.get("raw_statement_text")), code, path)
    if stage in {"DRAFT", "SUBMITTED"}:
        require(decision is None and record["classification"] == "PROPOSED_CLAIM" and record["review_outcome"] == "PENDING_REVIEW", code, path)
        return
    require(decision is not None, code, path)
    certified = stage == "EFFECTIVE"
    outcome = "RATIFIED_FOR_CASE" if certified else "NOT_ADOPTED_AS_CASE_CONSTRAINT"
    require(record["review_outcome"] == decision["review_outcome"] == outcome, code, path)
    require(record["classification"] == ("CASE_RATIFIED" if certified else "PROPOSED_CLAIM"), code, path)
    env = decision["effectiveness_envelope"]
    receipt = env["auth_check_receipt"]
    action = CERTIFY if certified else REJECT
    require(env["action_type"] == action and receipt["requested_power"] == POWERS[action], code, path)
    require(env["evaluated_target_version_seq"] + 1 == env["resulting_target_version_seq"] <= record["version_seq"], code, path)
    require(receipt["evaluated_target_version_seq"] == env["evaluated_target_version_seq"], code, path)
    require(env["target_object_id"] == receipt["target_object_id"] == record["object_id"], code, path)
    require(receipt["verification_status"] == "VERIFIED_ACTIVE" and receipt["authenticated_principal_id"] == decision["reviewer_principal_id"], code, path)
    require(env["mandate_ref"]["mandate_id"] == receipt["mandate_id"] and env["mandate_ref"]["version_hash"] == receipt["evaluated_version_hash"], code, path)
    require(all(env["scope_spec"][key] == record[key] for key in ["case_id", "tenant_node_id"])
            and env["scope_spec"]["target_object_id"] == record["object_id"], code, path)


def validate_semantics(data):
    before, after = data["before"], data["after"]
    old, new = before["constraint_record"], after["constraint_record"]
    cmd, ctx = data["command"], data["server_context"]
    action, payload = cmd["requested_action"], cmd["payload"]
    principal, now = ctx["authenticated_principal_id"], ctx["checked_at"]
    require(ctx["tenant_node_id"] == old["tenant_node_id"] and ctx["case_id"] == old["case_id"]
            and old["acl_policy_ref"] in ctx["acl_policy_refs"], "ERR_CONTEXT_SCOPE_MISMATCH", "/server_context")
    require(cmd["target_object_id"] == old["object_id"], "ERR_TARGET_BINDING_MISMATCH", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == old["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    require(new["version_seq"] == old["version_seq"] + 1, "ERR_VERSION_SEQ_MISMATCH", "/after/constraint_record/version_seq")
    validate_prior_state(before)

    evidence = {}
    for row in ctx["evidence_snapshots"]:
        key = (row["evidence_id"], row["revision"])
        require(key not in evidence, "ERR_DUPLICATE_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
        evidence[key] = row

    def check_ref(ref, path):
        require(evidence.get((ref["evidence_id"], ref["revision"])) == ref, "ERR_UNRESOLVED_EVIDENCE_REFERENCE", path)

    for state_name, state in [("before", old), ("after", new)]:
        if "source_ref" in state:
            check_ref(state["source_ref"], f"/{state_name}/constraint_record/source_ref")
        if "metric_spec" in state:
            check_ref(state["metric_spec"]["definition_ref"], f"/{state_name}/constraint_record/metric_spec/definition_ref")

    expected = copy.deepcopy(old)
    expected["version_seq"] += 1
    if action == STANCE:
        require(principal == old["claimant_principal_id"], "ERR_NOT_CLAIMANT_PRINCIPAL", "/server_context/authenticated_principal_id")
        require(old["constraint_stage"] in {"EFFECTIVE", "RESOLVED_NON_EFFECTIVE"}, "ERR_INVALID_TRANSITION", "/before/constraint_record/constraint_stage")
        require(after["review_decision_record"] == before["review_decision_record"], "ERR_HISTORICAL_REVIEW_DECISION_MUTATION", "/after/review_decision_record")
        require(new["effective_standing_status"] == old["effective_standing_status"], "ERR_EFFECTIVE_STANDING_MUTATION", "/after/constraint_record/effective_standing_status")
        expected["claimant_post_review_stance"].update(payload)
        expected["claimant_post_review_stance"]["stance_status"] = "RECORDED_BY_CLAIMANT"
    else:
        require(new["claimant_post_review_stance"] == old["claimant_post_review_stance"], "ERR_UNILATERAL_CLAIMANT_STANCE_MUTATION", "/after/constraint_record/claimant_post_review_stance")
        require(new["effective_standing_status"] == old["effective_standing_status"], "ERR_EFFECTIVE_STANDING_MUTATION", "/after/constraint_record/effective_standing_status")

    if action == SUBMIT:
        require(old["constraint_stage"] == "DRAFT", "ERR_INVALID_TRANSITION", "/before/constraint_record/constraint_stage")
        policy = ctx["intake_policy_check"]
        require(policy["result"] == "ALLOWED" and policy["authenticated_principal_id"] == principal
                and principal == old["claimant_principal_id"] and policy["target_object_id"] == old["object_id"]
                and policy["checked_at"] == now, "ERR_INTAKE_POLICY_DENIED", "/server_context/intake_policy_check")
        expected.update(constraint_stage="SUBMITTED", classification="PROPOSED_CLAIM")
        for key in ["claim_title", "raw_statement_text", "source_ref", "metric_spec"]:
            if key in payload:
                expected[key] = payload[key]
        require(after["review_decision_record"] is None, "ERR_TRANSITION_MISMATCH", "/after/review_decision_record")

    if action in {CERTIFY, REJECT}:
        require(old["constraint_stage"] == "SUBMITTED", "ERR_INVALID_TRANSITION", "/before/constraint_record/constraint_stage")
        receipt = ctx["generated_auth_receipt"]
        mandate = ctx["authority_snapshot"]
        claim = cmd["mandate_claim"]
        inp = payload["effectiveness_envelope_input"]
        power = POWERS[action]
        require(claim["claimed_power"] == receipt["requested_power"] == power, "ERR_UNAUTHORIZED_POWER_FOR_ACTION", "/command/mandate_claim/claimed_power")
        require(receipt["verification_status"] == "VERIFIED_ACTIVE", "ERR_RECEIPT_NOT_ACTIVE", "/server_context/generated_auth_receipt/verification_status")
        require(receipt["evaluated_target_version_seq"] == old["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/server_context/generated_auth_receipt/evaluated_target_version_seq")
        require(mandate["status"] == "ACTIVE" and mandate["valid_from"] <= now < mandate["valid_until"]
                and mandate["principal_id"] == principal and power in mandate["powers"]
                and mandate["mandate_id"] == claim["mandate_id"] and mandate["version_hash"] == claim["expected_version_hash"]
                and mandate["clause_reference"] == inp["mandate_clause_ref"]
                and mandate["tenant_node_id"] == old["tenant_node_id"] and mandate["case_id"] == old["case_id"]
                and mandate["target_object_id"] == old["object_id"], "ERR_MANDATE_SNAPSHOT_MISMATCH", "/server_context/authority_snapshot")
        bindings = {
            "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "idempotency_key": cmd["idempotency_key"],
            "authenticated_principal_id": principal, "mandate_id": mandate["mandate_id"],
            "evaluated_version_hash": mandate["version_hash"], "target_object_id": old["object_id"],
            "checked_at": now, "authority_source_id": mandate["authority_source_id"],
        }
        require(all(receipt[key] == value for key, value in bindings.items()), "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
        scope = inp["scope_spec"]
        require(scope["tenant_node_id"] == old["tenant_node_id"] and scope["case_id"] == old["case_id"]
                and scope["target_object_id"] == old["object_id"] and scope["effective_from"] <= now < scope["effective_until"], "ERR_SCOPE_MISMATCH", "/command/payload/effectiveness_envelope_input/scope_spec")
        for ref in inp["evidence_refs"]:
            check_ref(ref, "/command/payload/effectiveness_envelope_input/evidence_refs")
        check_ref(inp["remedy_spec"]["rollback_or_compensation_procedure_ref"], "/command/payload/effectiveness_envelope_input/remedy_spec")
        decision = after["review_decision_record"]
        require(decision is not None, "ERR_INCOMPLETE_FOUR_TUPLE", "/after/review_decision_record")
        env = decision["effectiveness_envelope"]
        require(env["evaluated_target_version_seq"] == old["version_seq"] and env["resulting_target_version_seq"] == new["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/after/review_decision_record/effectiveness_envelope")
        expected_env = {
            "envelope_id": env["envelope_id"], "action_type": action, "target_object_id": old["object_id"],
            "evaluated_target_version_seq": old["version_seq"], "resulting_target_version_seq": new["version_seq"],
            "mandate_ref": {"mandate_id": mandate["mandate_id"], "version_hash": mandate["version_hash"], "clause_reference": mandate["clause_reference"]},
            "auth_check_receipt": receipt, "evidence_refs": inp["evidence_refs"],
            "scope_spec": scope, "remedy_spec": inp["remedy_spec"],
        }
        require(env == expected_env, "ERR_ENVELOPE_BINDING_MISMATCH", "/after/review_decision_record/effectiveness_envelope")
        certified = action == CERTIFY
        outcome = "RATIFIED_FOR_CASE" if certified else "NOT_ADOPTED_AS_CASE_CONSTRAINT"
        if not certified:
            require(new["constraint_stage"] != "EFFECTIVE", "ERR_REJECTED_CLAIM_MARKED_EFFECTIVE", "/after/constraint_record/constraint_stage")
        expected.update(constraint_stage="EFFECTIVE" if certified else "RESOLVED_NON_EFFECTIVE", classification="CASE_RATIFIED" if certified else "PROPOSED_CLAIM", review_outcome=outcome)
        if certified:
            if "metric_spec" in payload:
                expected["metric_spec"] = payload["metric_spec"]
                check_ref(payload["metric_spec"]["definition_ref"], "/command/payload/metric_spec/definition_ref")
            require(old["measurement_kind"] != "QUANTITATIVE" or "metric_spec" in expected, "ERR_METRIC_SPEC_REQUIRED", "/command/payload/metric_spec")
        expected_decision = {
            "decision_id": decision["decision_id"], "version_seq": 1, "decision_stage": "EFFECTIVE",
            "review_outcome": outcome, "review_rationale": payload["review_rationale"],
            "reviewer_principal_id": principal, "effectiveness_envelope": expected_env,
        }
        require(decision == expected_decision, "ERR_TRANSITION_MISMATCH", "/after/review_decision_record")
    require(new == expected, "ERR_TRANSITION_MISMATCH", "/after/constraint_record")


def validate_transition(data):
    registry_result = check_registry()
    if not registry_result["is_valid"]:
        return registry_result
    for validator in (COMMON_VALIDATOR, VALIDATOR):
        error = first_schema_error(validator, data)
        if error:
            return error
    try:
        validate_semantics(data)
    except ContractFailure as error:
        return error.result
    return result("OFFLINE_SEMANTIC")


def validate_file(path):
    try:
        data = load_json(path)
    except OSError:
        return result("JSON_INPUT", "ERR_INPUT_UNREADABLE", "/")
    except ValueError:
        return result("JSON_INPUT", "ERR_INVALID_JSON", "/")
    return validate_transition(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.fixtures:
        report = validate_file(path)
        failed = failed or not report["is_valid"]
        print(json.dumps({"input": str(path), **report}, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
