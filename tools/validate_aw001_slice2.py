"""Offline Slice 2A protective-order contracts; no database or external execution."""

import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.validate_case_packet import FORMATS, load_json

SPEC = ROOT / "specs/aw001_slice2"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON_VALIDATOR = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN_VALIDATOR = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)
ISSUE, LIFT, EXTEND = "ISSUE_PROTECTIVE_ORDER", "LIFT_PROTECTIVE_ORDER", "EXTEND_PROTECTIVE_ORDER"
ACTIONS = (ISSUE, LIFT, EXTEND)
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION", "CHECK_FOUR_TUPLE_COMPLETE", "CHECK_AUTHORITY_SNAPSHOT", "CHECK_PROTECTION_SCOPE_AND_TIME", "CHECK_ALLOWED_WRITE_SET", "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITE_POLICIES = {ISSUE: "APPEND_NEW_ORDER_AND_ENVELOPE", LIFT: "LIFT_TARGET_AND_APPEND_ENVELOPE", EXTEND: "SUPERSEDE_TARGET_APPEND_NEW_ORDER_AND_ENVELOPE"}
ORDER_FIELDS = tuple(SCHEMA["$defs"]["OrderInput"]["properties"])


def report(layer, code=None, path=None):
    return {"is_valid": code is None, "validation_layer": layer, "error_code": code, "failed_path": path}


class ContractFailure(Exception):
    def __init__(self, code, path):
        self.report = report("OFFLINE_SEMANTIC", code, path)
        super().__init__(code)


def require(condition, code, path):
    if not condition:
        raise ContractFailure(code, path)


def digest(value):
    """Local JSON fingerprint; not JCS, not authentication or a privacy mechanism."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def schema_error(validator, data):
    def leaves(error):
        if error.context:
            for child in error.context:
                yield from leaves(child)
        else:
            yield error
    errors = [leaf for e in validator.iter_errors(data) for leaf in leaves(e)]
    if not errors:
        return None
    def key(e):
        unknown = list(e.absolute_path) == ["command", "requested_action"] and e.validator == "enum"
        return (not unknown, e.validator == "type" and e.validator_value == "null", pointer(e.absolute_path), str(e.validator), pointer(e.absolute_schema_path))
    e = min(errors, key=key)
    path = list(e.absolute_path)
    code = "ERR_SCHEMA_VALIDATION"
    if path == ["command", "requested_action"] and e.validator == "enum":
        code = "ERR_UNREGISTERED_ACTION_ID"
    elif path[:1] == ["command"] and e.validator == "additionalProperties":
        code = "ERR_CLIENT_FORBIDDEN_FIELD"
    elif e.validator in {"required", "minItems"}:
        missing = set(e.validator_value) - set(e.instance) if e.validator == "required" and isinstance(e.instance, dict) else set()
        if "effectiveness_envelope_input" in path or missing & {"effectiveness_envelope_input", "mandate_claim"}:
            code = "ERR_INCOMPLETE_FOUR_TUPLE"
    return report("JSON_SCHEMA", code, pointer(path))


def check_registry(path=DEFAULT_REGISTRY):
    try:
        r = load_json(path)
    except (OSError, ValueError):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if (not isinstance(r, dict) or set(r) != {"contract_version", "global_lock_order", "actions"}
            or r["contract_version"] != "0.3.0-rc1-slice2a" or not isinstance(r["actions"], dict) or set(r["actions"]) != set(ACTIONS)):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if r["global_lock_order"] != ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder", "FulfillmentTicket"]:
        return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/global_lock_order")
    for action in ACTIONS:
        e = r["actions"][action]
        p = "/actions/" + action
        if (not isinstance(e, dict) or set(e) != {"required_power", "requires_effectiveness_envelope", "required_validators", "lock_levels", "write_policy"}
                or e["required_power"] != action or e["requires_effectiveness_envelope"] is not True or e["write_policy"] != WRITE_POLICIES[action]):
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", p)
        checks = CHECKS + (["CHECK_EXECUTOR_CANNOT_LIFT"] if action == LIFT else [])
        if not isinstance(e["required_validators"], list) or any(c not in e["required_validators"] for c in checks):
            return report("REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING", p + "/required_validators")
        if e["required_validators"] != checks:
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", p)
        if e["lock_levels"] != [0, 1, 2, 3] or any(type(v) is not int for v in e["lock_levels"]):
            return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH", p + "/lock_levels")
    return report("REGISTRY_LOADER")


def domain_index(domain):
    resources, orders, envelopes = {}, {}, {}
    seen = set()
    for collection, key, index in [("resources", "object_id", resources), ("protective_orders", "order_id", orders), ("effectiveness_envelopes", "envelope_id", envelopes)]:
        for row in domain[collection]:
            require(row[key] not in seen, "ERR_DUPLICATE_OBJECT_ID", "/before/domain_objects/" + collection)
            seen.add(row[key]); index[row[key]] = row
    return resources, orders, envelopes


def ancestors(target_id, resources, orders):
    if target_id in orders:
        target = orders[target_id]
        return [target_id, target["case_id"]]
    require(target_id in resources, "ERR_UNKNOWN_TARGET", "/before/domain_objects/resources")
    chain, current = [], target_id
    while current is not None:
        require(current in resources and current not in chain, "ERR_INVALID_OBJECT_HIERARCHY", "/before/domain_objects/resources")
        chain.append(current)
        current = resources[current]["parent_object_id"]
    return chain


def covers(order, target_id, resources, orders):
    target = resources.get(target_id) or orders.get(target_id)
    require(target is not None, "ERR_UNKNOWN_TARGET", "/command/target_object_id")
    if (order["tenant_node_id"], order["case_id"]) != (target["tenant_node_id"], target["case_id"]):
        return False
    scope = [target_id] if order["scope_mode"] == "DIRECT_ONLY" else ancestors(target_id, resources, orders)
    return bool(set(scope) & set(order["protected_target_object_ids"]))


def active(order, now):
    return order["order_status"] == "ACTIVE_HOLD" and order["effective_from"] <= now < order["expires_at"]


def validate_domain(domain):
    resources, orders, envs = domain_index(domain)
    for ident, row in resources.items():
        chain = ancestors(ident, resources, orders)
        case = resources.get(row["case_id"])
        require(case is not None and case["kind"] == "CASE" and chain[-1] == row["case_id"], "ERR_INVALID_OBJECT_HIERARCHY", "/before/domain_objects/resources")
        require(all((resources[i]["tenant_node_id"], resources[i]["case_id"]) == (row["tenant_node_id"], row["case_id"]) for i in chain), "ERR_INVALID_OBJECT_HIERARCHY", "/before/domain_objects/resources")
        if row["kind"] == "CASE":
            require(ident == row["case_id"] and row["parent_object_id"] is None, "ERR_INVALID_OBJECT_HIERARCHY", "/before/domain_objects/resources")
        else:
            parent = resources.get(row["parent_object_id"])
            permitted = {"CASE"} if row["kind"] == "COMMITMENT" else {"CASE", "COMMITMENT"}
            require(parent is not None and parent["kind"] in permitted, "ERR_INVALID_OBJECT_HIERARCHY", "/before/domain_objects/resources")
    for oid, order in orders.items():
        path = "/before/domain_objects/protective_orders"
        require(order["effective_from"] < order["expires_at"], "ERR_INVALID_ORDER_HISTORY", path)
        case = resources.get(order["case_id"])
        require(case is not None and case["kind"] == "CASE" and case["tenant_node_id"] == order["tenant_node_id"], "ERR_INVALID_OBJECT_HIERARCHY", path)
        for target_id in order["protected_target_object_ids"]:
            target = resources.get(target_id)
            require(target is not None and (target["tenant_node_id"], target["case_id"]) == (order["tenant_node_id"], order["case_id"]), "ERR_INVALID_OBJECT_HIERARCHY", path)
        env = envs.get(order["issuance_envelope_id"])
        require(env is not None and env["action_type"] in {ISSUE, EXTEND}, "ERR_INVALID_ORDER_HISTORY", path)
        scope = env["scope_spec"]
        require((scope["tenant_node_id"], scope["case_id"]) == (order["tenant_node_id"], order["case_id"])
                and scope["effective_from"] <= env["auth_check_receipt"]["checked_at"] <= order["effective_from"]
                and order["expires_at"] <= scope["effective_until"], "ERR_INVALID_ORDER_HISTORY", path)
        if env["action_type"] == ISSUE:
            require(env["target_object_id"] == order["case_id"], "ERR_INVALID_ORDER_HISTORY", path)
        else:
            predecessor = orders.get(env["target_object_id"])
            require(predecessor is not None and predecessor["superseded_by_order_id"] == oid
                    and predecessor["disposition_envelope_id"] == env["envelope_id"], "ERR_INVALID_ORDER_HISTORY", path)
        require(env["order_spec_sha256"] == digest({key: order[key] for key in ORDER_FIELDS}) and {"object_id": oid, "version_seq": 1} in env["resulting_versions"], "ERR_INVALID_ORDER_HISTORY", path)
        if order["order_status"] in {"ACTIVE_HOLD", "EXPIRED_NO_LONGER_BINDING"}:
            require(order["disposition_envelope_id"] is None and order["superseded_by_order_id"] is None, "ERR_INVALID_ORDER_HISTORY", path)
        else:
            disposition = envs.get(order["disposition_envelope_id"])
            expected_action = LIFT if order["order_status"] == "LIFTED_BY_DECISION" else EXTEND
            require(disposition is not None and disposition["action_type"] == expected_action and disposition["target_object_id"] == oid
                    and disposition["evaluated_target_version_seq"] + 1 == order["version_seq"]
                    and {"object_id": oid, "version_seq": order["version_seq"]} in disposition["resulting_versions"], "ERR_INVALID_ORDER_HISTORY", path)
            if expected_action == EXTEND:
                successor = orders.get(order["superseded_by_order_id"])
                require(successor is not None and successor["issuance_envelope_id"] == order["disposition_envelope_id"] and successor["expires_at"] > order["expires_at"], "ERR_INVALID_ORDER_HISTORY", path)
            else:
                require(order["superseded_by_order_id"] is None, "ERR_INVALID_ORDER_HISTORY", path)
    for env in envs.values():
        receipt = env["auth_check_receipt"]
        require(receipt["verification_status"] == "VERIFIED_ACTIVE" and receipt["requested_power"] == env["action_type"]
                and receipt["target_object_id"] == env["target_object_id"] and receipt["evaluated_target_version_seq"] == env["evaluated_target_version_seq"]
                and receipt["mandate_id"] == env["mandate_ref"]["mandate_id"] and receipt["evaluated_version_hash"] == env["mandate_ref"]["version_hash"], "ERR_INVALID_ORDER_HISTORY", "/before/domain_objects/effectiveness_envelopes")
    return resources, orders, envs


def evaluate_protective_orders(domain, target_id, power, evaluated_at):
    """Pure read-only predicate; does not authorize the target operation."""
    require(DOMAIN_VALIDATOR.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    require(isinstance(target_id, str) and power in SCHEMA["$defs"]["Power"]["enum"], "ERR_INVALID_GATE_ARGUMENT", "/power")
    require(isinstance(evaluated_at, str) and FORMATS.conforms(evaluated_at, "date-time"), "ERR_INVALID_GATE_ARGUMENT", "/evaluated_at")
    resources, orders, _ = validate_domain(domain)
    ancestors(target_id, resources, orders)
    return sorted(oid for oid, order in orders.items() if active(order, evaluated_at) and power in order["blocked_powers"] and covers(order, target_id, resources, orders))


def authority_status(cmd, ctx, resources, orders):
    if ctx["authority_source_status"] == "UNAVAILABLE":
        return "SOURCE_UNAVAILABLE"
    mandate = ctx["authority_snapshot"]
    if mandate is None:
        return "UNKNOWN_MANDATE"
    claim = cmd["mandate_claim"]
    if (mandate["mandate_id"] != claim["mandate_id"] or mandate["version_hash"] != claim["expected_version_hash"]
            or mandate["principal_id"] != ctx["authenticated_principal_id"] or mandate["tenant_node_id"] != ctx["tenant_node_id"]
            or mandate["case_id"] != ctx["case_id"] or mandate["clause_reference"] != cmd["payload"]["effectiveness_envelope_input"]["mandate_clause_ref"]):
        return "SCOPE_MISMATCH"
    if mandate["status"] == "REVOKED":
        return "REVOKED"
    if ctx["evaluated_at"] >= mandate["valid_until"]:
        return "EXPIRED"
    if ctx["evaluated_at"] < mandate["valid_from"]:
        return "SCOPE_MISMATCH"
    if cmd["requested_action"] not in mandate["powers"] or claim["claimed_power"] != cmd["requested_action"]:
        return "POWER_NOT_GRANTED"
    if not set(ancestors(cmd["target_object_id"], resources, orders)) & set(mandate["allowed_target_ids"]):
        return "SCOPE_MISMATCH"
    return "VERIFIED_ACTIVE"


def validate_basis(data):
    cmd, ctx, before = data["command"], data["server_context"], data["before"]
    resources, orders, envs = validate_domain(before["domain_objects"])
    require(ctx["access_granted"], "ERR_CONTEXT_ACCESS_DENIED", "/server_context/access_granted")
    case = resources.get(ctx["case_id"])
    target = resources.get(cmd["target_object_id"]) or orders.get(cmd["target_object_id"])
    require(case is not None and case["kind"] == "CASE" and case["tenant_node_id"] == ctx["tenant_node_id"] and target is not None
            and (target["tenant_node_id"], target["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"]), "ERR_CONTEXT_SCOPE_MISMATCH", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    require((cmd["requested_action"] == ISSUE and target is case) or (cmd["requested_action"] != ISSUE and cmd["target_object_id"] in orders), "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    record, attempt = before["command_record"], before["command_attempt"]
    require(record["status"] == "IN_FLIGHT" and record["result_id"] is None and attempt["status"] == "IN_FLIGHT", "ERR_TERMINAL_COMMAND_REEXECUTION", "/before/command_record")
    expected = {"command_id": cmd["command_id"], "tenant_node_id": ctx["tenant_node_id"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "idempotency_key": cmd["idempotency_key"], "request_sha256": digest(cmd), "current_attempt_id": ctx["attempt_id"], "generation": ctx["generation"]}
    require(all(record[k] == v for k, v in expected.items()), "ERR_COMMAND_BINDING_MISMATCH", "/before/command_record")
    require(attempt["attempt_id"] == ctx["attempt_id"] and attempt["command_id"] == cmd["command_id"] and attempt["generation"] == record["generation"], "ERR_ATTEMPT_GENERATION_MISMATCH", "/before/command_attempt")
    require(ctx["new_envelope_id"] not in set(resources) | set(orders) | set(envs), "ERR_DUPLICATE_OBJECT_ID", "/server_context/new_envelope_id")
    evidence = {}
    for ref in ctx["evidence_snapshots"]:
        key = (ref["evidence_id"], ref["revision"])
        require(key not in evidence, "ERR_DUPLICATE_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
        evidence[key] = ref
    inp = cmd["payload"]["effectiveness_envelope_input"]
    refs = inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    if "new_order" in cmd["payload"]:
        refs.append(cmd["payload"]["new_order"]["source_reference"])
    for ref in refs:
        require(evidence.get((ref["evidence_id"], ref["revision"])) == ref, "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/command/payload/effectiveness_envelope_input")
    return resources, orders, envs


def decision_diagnostics(data, resources, orders):
    cmd, ctx = data["command"], data["server_context"]
    now, action = ctx["evaluated_at"], cmd["requested_action"]
    matched = sorted(oid for oid, order in orders.items() if active(order, now) and action in order["blocked_powers"] and covers(order, cmd["target_object_id"], resources, orders))
    status = authority_status(cmd, ctx, resources, orders)
    receipt = ctx["generated_auth_receipt"]
    require(receipt is not None, "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
    bindings = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "idempotency_key": cmd["idempotency_key"], "authenticated_principal_id": ctx["authenticated_principal_id"], "mandate_id": cmd["mandate_claim"]["mandate_id"], "evaluated_version_hash": cmd["mandate_claim"]["expected_version_hash"], "requested_power": action, "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "checked_at": now, "authority_source_id": ctx["authority_source_id"], "domain_sha256": digest(data["before"]["domain_objects"]), "verification_status": status}
    require(all(receipt[k] == v for k, v in bindings.items()), "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
    codes = ["ERR_BLOCKED_BY_PROTECTIVE_ORDER"] if matched else []
    status_codes = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE", "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED", "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED", "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER", "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH", "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
    if status != "VERIFIED_ACTIVE":
        codes.append(status_codes[status])
    scope = cmd["payload"]["effectiveness_envelope_input"]["scope_spec"]
    if (scope["tenant_node_id"], scope["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]) or not scope["effective_from"] <= now < scope["effective_until"]:
        codes.append("ERR_EFFECTIVENESS_SCOPE_MISMATCH")
    if action in {ISSUE, EXTEND}:
        new = cmd["payload"]["new_order"]
        if new["order_id"] in set(resources) | set(orders) | {e["envelope_id"] for e in data["before"]["domain_objects"]["effectiveness_envelopes"]} or new["order_id"] == ctx["new_envelope_id"]:
            codes.append("ERR_NEW_OBJECT_ID_CONFLICT")
        if not now <= new["effective_from"] < new["expires_at"]:
            codes.append("ERR_ORDER_INTERVAL_INVALID")
        if not scope["effective_from"] <= new["effective_from"] < new["expires_at"] <= scope["effective_until"]:
            codes.append("ERR_EFFECTIVENESS_SCOPE_MISMATCH")
        targets = [resources.get(ident) for ident in new["protected_target_object_ids"]]
        if any(t is None or (t["tenant_node_id"], t["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]) for t in targets):
            codes.append("ERR_ORDER_TARGET_SCOPE_MISMATCH")
        elif status == "VERIFIED_ACTIVE":
            mandate = ctx["authority_snapshot"]
            if any(not set(ancestors(t["object_id"], resources, orders)) & set(mandate["allowed_target_ids"]) for t in targets) or not set(new["blocked_powers"]) <= set(mandate["allowed_blocked_powers"]):
                codes.append("ERR_ORDER_EXCEEDS_MANDATE")
            duration = datetime.fromisoformat(new["expires_at"]) - datetime.fromisoformat(new["effective_from"])
            if duration.total_seconds() > mandate["max_order_duration_hours"] * 3600:
                codes.append("ERR_ORDER_EXCEEDS_MANDATE")
        if action == EXTEND:
            old = orders[cmd["target_object_id"]]
            if old["order_status"] not in {"ACTIVE_HOLD", "EXPIRED_NO_LONGER_BINDING"}:
                codes.append("ERR_ORDER_NOT_EXTENDABLE")
            if new["expires_at"] <= old["expires_at"] or new["effective_from"] < old["effective_from"] or any(new[k] != old[k] for k in ["protected_target_object_ids", "blocked_powers", "scope_mode", "execution_boundary"]):
                codes.append("ERR_EXTENSION_SCOPE_OR_TIME_MISMATCH")
    else:
        old = orders[cmd["target_object_id"]]
        if not active(old, now):
            codes.append("ERR_EXPIRED_OR_INACTIVE_ORDER")
        mappings = {m["principal_id"]: m for m in ctx["member_mappings"]}
        require(len(mappings) == len(ctx["member_mappings"]), "ERR_MEMBER_MAPPING_CONFLICT", "/server_context/member_mappings")
        member = mappings.get(ctx["authenticated_principal_id"])
        tickets = [r for r in resources.values() if r["kind"] == "TICKET" and covers(old, r["object_id"], resources, orders)]
        if member is None or member["status"] != "VERIFIED" or member["member_id"] is None or member["member_id"] != ctx["authenticated_member_id"] or any(t["executor_member_id"] is None for t in tickets):
            codes.append("ERR_SOD_IDENTITY_UNMAPPED")
        elif any(t["executor_member_id"] == member["member_id"] for t in tickets):
            codes.append("ERR_SOD_EXECUTOR_CANNOT_LIFT_HOLD")
    return list(dict.fromkeys(codes)), matched


def expected_applied_domain(data):
    cmd, ctx = data["command"], data["server_context"]
    domain = copy.deepcopy(data["before"]["domain_objects"])
    action, new = cmd["requested_action"], cmd["payload"].get("new_order")
    versions = []
    if action != ISSUE:
        old = next(o for o in domain["protective_orders"] if o["order_id"] == cmd["target_object_id"])
        old["version_seq"] += 1
        old["order_status"] = "LIFTED_BY_DECISION" if action == LIFT else "SUPERSEDED_BY_NEW_ORDER"
        old["disposition_envelope_id"] = ctx["new_envelope_id"]
        old["superseded_by_order_id"] = None if action == LIFT else new["order_id"]
        versions.append({"object_id": old["order_id"], "version_seq": old["version_seq"]})
    if new is not None:
        domain["protective_orders"].append({**copy.deepcopy(new), "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"], "version_seq": 1, "order_status": "ACTIVE_HOLD", "issuance_envelope_id": ctx["new_envelope_id"], "disposition_envelope_id": None, "superseded_by_order_id": None})
        versions.append({"object_id": new["order_id"], "version_seq": 1})
    versions.sort(key=lambda row: row["object_id"])
    inp = cmd["payload"]["effectiveness_envelope_input"]
    env = {"envelope_id": ctx["new_envelope_id"], "action_type": action, "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "resulting_versions": versions, "mandate_ref": {"mandate_id": cmd["mandate_claim"]["mandate_id"], "version_hash": cmd["mandate_claim"]["expected_version_hash"], "clause_reference": inp["mandate_clause_ref"]}, "auth_check_receipt": copy.deepcopy(ctx["generated_auth_receipt"]), "evidence_refs": copy.deepcopy(inp["evidence_refs"]), "scope_spec": copy.deepcopy(inp["scope_spec"]), "remedy_spec": copy.deepcopy(inp["remedy_spec"]), "order_spec_sha256": digest(new) if new is not None else None}
    domain["effectiveness_envelopes"].append(env)
    return domain, versions


def validate_semantics(data):
    resources, orders, _ = validate_basis(data)
    before, ctx, cmd, after = data["before"], data["server_context"], data["command"], data["after"]
    out = after["execution_outcome"]
    observation = ctx["execution_observation"]
    if observation != "NORMAL":
        unknown = observation == "COMMIT_RESPONSE_LOST"
        require(ctx["generated_auth_receipt"] is None, "ERR_FAULT_CLAIMS_COMPLETED_AUTH", "/server_context/generated_auth_receipt")
        require(out["outcome"] == ("OUTCOME_UNKNOWN" if unknown else "SYSTEM_FAULT"), "ERR_FAULT_OUTCOME_MISMATCH", "/after/execution_outcome/outcome")
        require(after["domain_objects"] == (None if unknown else before["domain_objects"]), "ERR_FAULT_CLAIMS_DOMAIN_STATE", "/after/domain_objects")
        require(out["command_record"] == (None if unknown else before["command_record"]) and out["command_attempt"] == (None if unknown else before["command_attempt"]), "ERR_FAULT_REWRITES_COMMAND_STATE", "/after/execution_outcome")
        expected_fault = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "observed_at": ctx["evaluated_at"], "kind": observation, "recovery_action": "RECONCILE_REQUIRED" if unknown else "RETRY_AFTER_RECONCILIATION", "persistence_claim": "NOT_ASSERTED"}
        require(out["fault_observation"] == expected_fault, "ERR_FAULT_OBSERVATION_MISMATCH", "/after/execution_outcome/fault_observation")
        return
    codes, matched = decision_diagnostics(data, resources, orders)
    rejected = bool(codes)
    expected_status = "REJECTED" if rejected else "APPLIED"
    require(out["outcome"] == expected_status, "ERR_DECLARED_OUTCOME_MISMATCH", "/after/execution_outcome/outcome")
    if rejected:
        domain, versions = before["domain_objects"], []
        require(after["domain_objects"] == domain, "ERR_REJECTED_DOMAIN_MUTATION", "/after/domain_objects")
        rejection = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "rejection_error_code": codes[0], "diagnostic_codes": codes, "blocking_protective_order_ids": matched}
        require(out["command_rejection"] == rejection, "ERR_REJECTION_DIAGNOSTIC_MISMATCH", "/after/execution_outcome/command_rejection")
    else:
        domain, versions = expected_applied_domain(data)
        require(after["domain_objects"] == domain, "ERR_APPLIED_WRITE_SET_MISMATCH", "/after/domain_objects")
        validate_domain(domain)
    require(out["auth_check_receipt"] == ctx["generated_auth_receipt"], "ERR_RECEIPT_BINDING_MISMATCH", "/after/execution_outcome/auth_check_receipt")
    retryable = codes == ["ERR_MANDATE_SOURCE_UNAVAILABLE"]
    result_record = out["command_result"]
    require(result_record is not None, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
    expected_result = {"result_id": result_record["result_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "status": expected_status, "retry_disposition": "NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY" if retryable else "FINAL", "applied_versions": versions}
    require(result_record == expected_result, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
    coord = copy.deepcopy(before["command_record"])
    coord.update(version_seq=coord["version_seq"] + 1, status="IN_FLIGHT" if retryable else expected_status, result_id=result_record["result_id"])
    attempt = copy.deepcopy(before["command_attempt"])
    attempt.update(version_seq=attempt["version_seq"] + 1, status="FAILED_RETRYABLE" if retryable else expected_status)
    require(out["command_record"] == coord and out["command_attempt"] == attempt, "ERR_COMMAND_RESULT_BINDING_MISMATCH", "/after/execution_outcome")
    audit = out["audit_event"]
    expected_audit = {"audit_id": audit["audit_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "outcome": expected_status, "checked_at": ctx["evaluated_at"], "before_domain_sha256": digest(before["domain_objects"]), "after_domain_sha256": digest(domain), "written_versions": versions}
    require(audit == expected_audit, "ERR_AUDIT_BINDING_MISMATCH", "/after/execution_outcome/audit_event")


def validate_transition(data):
    registry = check_registry()
    if not registry["is_valid"]:
        return registry
    for validator in [COMMON_VALIDATOR, VALIDATOR]:
        error = schema_error(validator, data)
        if error:
            return error
    try:
        validate_semantics(data)
    except ContractFailure as error:
        return error.report
    return report("OFFLINE_SEMANTIC")


def validate_file(path):
    try:
        data = load_json(path)
    except OSError:
        return report("JSON_INPUT", "ERR_INPUT_UNREADABLE", "/")
    except ValueError:
        return report("JSON_INPUT", "ERR_INVALID_JSON", "/")
    return validate_transition(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.fixtures:
        value = validate_file(path)
        failed |= not value["is_valid"]
        print(json.dumps({"input": str(path), **value}, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
