"""Slice 4A offline synthetic governance markings, derived outputs and lineage.

Validation only: never enforces real access control, never runs a graph engine,
and never exports or recovers real material.
"""

import argparse
import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import validate_aw001_slice2b as b

report, require, digest, ContractFailure = b.report, b.require, b.digest, b.ContractFailure
load_json, FORMATS = b.load_json, b.FORMATS
SPEC = ROOT / "specs/aw001_slice4/4a"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

SET, RELAX, DERIVE, EDGE, REVIEW = (
    "SET_GOVERNANCE_MARKINGS", "RELAX_GOVERNANCE_MARKINGS", "RECORD_DERIVED_OUTPUT",
    "RECORD_LINEAGE_EDGE", "RECORD_DEPENDENCY_REVIEW")
ACTIONS = (SET, RELAX, DERIVE, EDGE, REVIEW)
POWERS = {SET: "SET_GOVERNANCE_MARKINGS", RELAX: "RELAX_GOVERNANCE_MARKINGS",
          DERIVE: "RECORD_DERIVED_OUTPUT", EDGE: "RECORD_LINEAGE_EDGE",
          REVIEW: "RECORD_DEPENDENCY_REVIEW"}
PAYLOADS = {SET: "Payload0", RELAX: "Payload1", DERIVE: "Payload2", EDGE: "Payload3",
            REVIEW: "Payload4"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_MARKING_MONOTONICITY",
          "CHECK_DERIVED_MARKINGS", "CHECK_LINEAGE_DAG_AND_CASE_SCOPE",
          "CHECK_ALLOWED_WRITE_SET", "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {SET: "ADD_OR_TIGHTEN_MARKING", RELAX: "RELAX_MARKING",
          DERIVE: "CREATE_DERIVED_OUTPUT", EDGE: "CREATE_LINEAGE_EDGE",
          REVIEW: "CREATE_DEPENDENCY_REVIEW"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
AI_RANK = {"ALLOWED": 0, "RESTRICTED": 1, "FORBIDDEN": 2}
RETENTION_RANK = {"SHORT": 0, "STANDARD": 1, "LONG": 2, "LEGAL_HOLD": 3}


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


# ---------------------------------------------------------------- registry
def check_registry(path=DEFAULT_REGISTRY):
    try:
        value = load_json(path)
    except (ValueError, OSError):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    try:
        return _check_registry_value(value)
    except ContractFailure as error:
        return {**error.report, "validation_layer": "REGISTRY_LOADER"}


def _check_registry_value(value):
    require(isinstance(value, dict), "ERR_ACTION_REGISTRY_VIOLATION", "/")
    require(set(value) == {"contract_version", "global_lock_order", "actions"},
            "ERR_ACTION_REGISTRY_VIOLATION", "/")
    require(value["contract_version"] == "0.5.0-rc1-slice4a",
            "ERR_ACTION_REGISTRY_VIOLATION", "/contract_version")
    require(value["global_lock_order"] == LOCK_ORDER, "ERR_REGISTRY_LOCK_ORDER_MISMATCH",
            "/global_lock_order")
    require(isinstance(value["actions"], dict) and set(value["actions"]) == set(ACTIONS),
            "ERR_ACTION_REGISTRY_VIOLATION", "/actions")
    for action in ACTIONS:
        entry, base = value["actions"][action], "/actions/" + action
        require(isinstance(entry, dict) and set(entry) == {
            "required_power", "requires_effectiveness_envelope", "required_validators",
            "lock_levels", "write_policy"}, "ERR_ACTION_REGISTRY_VIOLATION", base)
        require(entry["required_power"] == POWERS[action]
                and entry["requires_effectiveness_envelope"] is True
                and entry["write_policy"] == WRITES[action],
                "ERR_ACTION_REGISTRY_VIOLATION", base)
        require(entry["required_validators"] == CHECKS,
                "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING", base + "/required_validators")
        require(entry["lock_levels"] == [0, 1, 2, 3, 4]
                and all(type(level) is int for level in entry["lock_levels"]),
                "ERR_REGISTRY_LOCK_ORDER_MISMATCH", base + "/lock_levels")
    return report("REGISTRY_LOADER")


def payload_validator(action):
    return Draft202012Validator({**SCHEMA, "$ref": "#/$defs/" + PAYLOADS[action]},
                                format_checker=FORMATS)


def validate_payload(command):
    action = command.get("requested_action")
    if action not in PAYLOADS:
        return None
    errors = [leaf for error in payload_validator(action).iter_errors(command.get("payload"))
              for leaf in (error.context or [error])]
    if not errors:
        return None
    error = errors[0]
    code = "ERR_SCHEMA_VALIDATION"
    if error.validator == "additionalProperties":
        code = "ERR_CLIENT_FORBIDDEN_FIELD"
    elif error.validator == "required" and "effectiveness_envelope_input" in set(error.validator_value):
        code = "ERR_INCOMPLETE_FOUR_TUPLE"
    return report("JSON_SCHEMA", code, pointer(["command", "payload", *error.absolute_path]))


# ---------------------------------------------------------------- markings
def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"],
                                  "kind": "PROTECTIVE_ORDER"}
    for row in domain["derived_outputs"]:
        index[row["output_id"]] = row
    return index


def object_case(domain, object_id):
    objects = object_index(domain)
    row = objects.get(object_id)
    if row is None:
        return None
    return row.get("case_id")


def latest_markings(domain):
    result = {}
    for row in sorted(domain["governance_markings"],
                      key=lambda item: item["policy_version_seq"]):
        result[row["object_id"]] = row
    return result


def marking_dims(row):
    return {"access_requirements": set(row["access_requirements"]),
            "allowed_purposes": set(row["allowed_purposes"]),
            "ai_processing": row["ai_processing"],
            "export_policy": row["export_policy"],
            "retention_class": row["retention_class"]}


def compare_tightening(old, new):
    """Return (tightened, relaxed) dimension booleans for a marking update."""
    tightened = relaxed = False
    old_dims, new_dims = marking_dims(old), marking_dims(new)
    if new_dims["access_requirements"] > old_dims["access_requirements"]:
        tightened = True
    elif new_dims["access_requirements"] < old_dims["access_requirements"]:
        relaxed = True
    if new_dims["allowed_purposes"] < old_dims["allowed_purposes"]:
        tightened = True
    elif new_dims["allowed_purposes"] > old_dims["allowed_purposes"]:
        relaxed = True
    if AI_RANK[new_dims["ai_processing"]] > AI_RANK[old_dims["ai_processing"]]:
        tightened = True
    elif AI_RANK[new_dims["ai_processing"]] < AI_RANK[old_dims["ai_processing"]]:
        relaxed = True
    if AI_RANK[new_dims["export_policy"]] > AI_RANK[old_dims["export_policy"]]:
        tightened = True
    elif AI_RANK[new_dims["export_policy"]] < AI_RANK[old_dims["export_policy"]]:
        relaxed = True
    if RETENTION_RANK[new_dims["retention_class"]] > RETENTION_RANK[old_dims["retention_class"]]:
        tightened = True
    elif RETENTION_RANK[new_dims["retention_class"]] < RETENTION_RANK[old_dims["retention_class"]]:
        relaxed = True
    return tightened, relaxed


def compute_effective_markings(domain, source_refs):
    latest = latest_markings(domain)
    rows = []
    for ref in source_refs:
        marking = latest.get(ref["object_id"])
        require(marking is not None, "ERR_MARKINGS_MISSING",
                "/command/payload/source_refs")
        rows.append(marking)
    access = sorted({item for row in rows for item in row["access_requirements"]})
    purposes = set(rows[0]["allowed_purposes"])
    for row in rows[1:]:
        purposes &= set(row["allowed_purposes"])
    require(bool(purposes), "ERR_DERIVATION_PURPOSE_EMPTY", "/command/payload/source_refs")
    return {"access_requirements": access, "allowed_purposes": sorted(purposes),
            "ai_processing": max((row["ai_processing"] for row in rows),
                                 key=lambda value: AI_RANK[value]),
            "export_policy": max((row["export_policy"] for row in rows),
                                 key=lambda value: AI_RANK[value]),
            "retention_class": max((row["retention_class"] for row in rows),
                                   key=lambda value: RETENTION_RANK[value]),
            "cross_case_policy": "SINGLE_CASE_ONLY"}


def case_policy(domain, case_id):
    return next((row for row in domain["case_access_policies"]
                 if row["case_id"] == case_id), None)


def policy_version(domain, case_id):
    row = case_policy(domain, case_id)
    return row["access_policy_version"] if row else 0


# ---------------------------------------------------------------- basis
def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


def authority_status(data, principal, mandate_id, power, expected_hash=None):
    cmd, ctx = data["command"], data["server_context"]
    if ctx["authority_source_status"] != "AVAILABLE":
        return "SOURCE_UNAVAILABLE"
    mandate = next((m for m in ctx["authority_snapshots"] if m["mandate_id"] == mandate_id), None)
    if mandate is None:
        return "UNKNOWN_MANDATE"
    if mandate["status"] == "REVOKED":
        return "REVOKED"
    if not mandate["valid_from"] <= ctx["evaluated_at"] < mandate["valid_until"]:
        return "EXPIRED"
    if power not in mandate["powers"]:
        return "POWER_NOT_GRANTED"
    if (mandate["principal_id"] != principal
            or (expected_hash is not None and mandate["version_hash"] != expected_hash)
            or (mandate["tenant_node_id"], mandate["case_id"])
            != (ctx["tenant_node_id"], ctx["case_id"])
            or not {cmd["target_object_id"], ctx["case_id"]}
            & set(mandate["allowed_target_ids"])):
        return "SCOPE_MISMATCH"
    return "VERIFIED_ACTIVE"


def current_receipt(data):
    cmd, ctx = data["command"], data["server_context"]
    action = cmd["requested_action"]
    claim = cmd["mandate_claim"]
    require(claim is not None and ctx["generated_auth_receipt"] is not None
            and ctx["new_envelope_id"] is not None, "ERR_INCOMPLETE_FOUR_TUPLE",
            "/server_context")
    receipt = ctx["generated_auth_receipt"]
    status = authority_status(data, ctx["authenticated_principal_id"], claim["mandate_id"],
                              POWERS[action], claim["expected_version_hash"])
    expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"],
                "generation": ctx["generation"], "idempotency_key": cmd["idempotency_key"],
                "authenticated_principal_id": ctx["authenticated_principal_id"],
                "mandate_id": claim["mandate_id"],
                "evaluated_version_hash": claim["expected_version_hash"],
                "requested_power": POWERS[action], "target_object_id": cmd["target_object_id"],
                "evaluated_target_version_seq": cmd["target_expected_version_seq"],
                "checked_at": ctx["evaluated_at"],
                "authority_source_id": ctx["authority_source_id"],
                "domain_sha256": digest(data["before"]["domain_objects"]),
                "context_sha256": context_digest(ctx),
                "verification_status": status}
    require(all(receipt[k] == v for k, v in expected.items()), "ERR_RECEIPT_BINDING_MISMATCH",
            "/server_context/generated_auth_receipt")
    return status


def validate_basis(data):
    cmd, ctx, before = data["command"], data["server_context"], data["before"]
    objects = validate_domain(before["domain_objects"])
    case = next((row for row in before["domain_objects"]["operations"]["cases"]
                 if row["object_id"] == ctx["case_id"]), None)
    require(ctx["access_granted"] and case is not None
            and case["tenant_node_id"] == ctx["tenant_node_id"],
            "ERR_CONTEXT_SCOPE_MISMATCH", "/command/target_object_id")
    target = objects.get(cmd["target_object_id"])
    require(target is not None, "ERR_CONTEXT_SCOPE_MISMATCH", "/command/target_object_id")
    kind = ("CASE" if target.get("kind") == "CASE" and target.get("object_id") == ctx["case_id"]
            else "OBJECT")
    if cmd["requested_action"] in {DERIVE, EDGE, REVIEW}:
        require(kind == "CASE", "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target["version_seq"],
            "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    deps = [{"object_id": case["object_id"], "version_seq": case["version_seq"]}]
    require(cmd["dependency_versions"] == deps, "ERR_DEPENDENCY_VERSION_MISMATCH",
            "/command/dependency_versions")
    record, attempt = before["command_record"], before["command_attempt"]
    expected = {"command_id": cmd["command_id"], "tenant_node_id": ctx["tenant_node_id"],
                "principal_id": ctx["authenticated_principal_id"],
                "action_id": cmd["requested_action"],
                "idempotency_key": cmd["idempotency_key"],
                "request_sha256": digest(cmd), "current_attempt_id": ctx["attempt_id"],
                "generation": ctx["generation"]}
    require(record["status"] == attempt["status"] == "IN_FLIGHT" and record["result_id"] is None
            and all(record[k] == v for k, v in expected.items()),
            "ERR_COMMAND_BINDING_MISMATCH", "/before/command_record")
    require(attempt["attempt_id"] == ctx["attempt_id"]
            and attempt["command_id"] == cmd["command_id"]
            and attempt["generation"] == ctx["generation"],
            "ERR_ATTEMPT_GENERATION_MISMATCH", "/before/command_attempt")
    for collection, key in (("member_directory", "principal_id"),
                            ("authority_snapshots", "mandate_id")):
        require(len({row[key] for row in ctx[collection]}) == len(ctx[collection]),
                "ERR_CONTEXT_SNAPSHOT_CONFLICT", "/server_context/" + collection)
    inp = cmd["payload"]["effectiveness_envelope_input"]
    refs = [member["source_ref"] for member in ctx["member_directory"]]
    refs += inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    if cmd["requested_action"] == RELAX:
        refs.append(cmd["payload"]["relaxation_review"]["evidence_ref"])
    require(all(evidence_ref_resolved(ctx, ref) for ref in refs),
            "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    return objects, target, case


# ---------------------------------------------------------------- domain
def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    objects = object_index(domain)
    ids = [row["marking_id"] for row in domain["governance_markings"]]
    ids += [row["output_id"] for row in domain["derived_outputs"]]
    ids += [row["edge_id"] for row in domain["lineage_edges"]]
    ids += [row["review_id"] for row in domain["dependency_reviews"]]
    ids += [row["envelope_id"] for row in domain["effectiveness_envelopes"]]
    operation_ids = [row["object_id"] for name in
                     ("cases", "resource_pools", "commitments", "tickets")
                     for row in domain["operations"][name]]
    operation_ids += [row["order_id"] for row in domain["operations"]["protective_orders"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["protection_envelopes"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["effectiveness_envelopes"]]
    require(len(ids) == len(set(ids)) and not set(ids) & set(operation_ids),
            "ERR_DUPLICATE_OBJECT_ID", "/domain_objects")
    policies = {row["case_id"]: row for row in domain["case_access_policies"]}
    require(len(policies) == len(domain["case_access_policies"]), "ERR_DUPLICATE_OBJECT_ID",
            "/case_access_policies")
    envelopes = {row["envelope_id"]: row for row in domain["effectiveness_envelopes"]}
    referenced = set()
    markings_by_object = {}
    for row in sorted(domain["governance_markings"],
                      key=lambda item: item["policy_version_seq"]):
        require(row["object_id"] in objects, "ERR_MARKING_TARGET_UNKNOWN",
                "/governance_markings")
        require(row["case_id"] == object_case(domain, row["object_id"]),
                "ERR_CROSS_CASE_REFERENCE", "/governance_markings")
        marking = envelopes.get(row["created_envelope_id"])
        require(marking is not None and marking["action_type"] in {SET, RELAX}
                and marking["target_object_id"] == row["object_id"]
                and {"object_id": row["marking_id"], "version_seq": row["version_seq"]}
                in marking["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/governance_markings")
        referenced.add(row["created_envelope_id"])
        markings_by_object.setdefault(row["object_id"], []).append(row)
    for rows in markings_by_object.values():
        require([row["version_seq"] for row in rows] == list(range(1, len(rows) + 1)),
                "ERR_MARKING_UPDATE_NOT_MONOTONIC", "/governance_markings")
    for case_id in {row["case_id"] for row in domain["governance_markings"]}:
        expected_version = len([row for row in domain["governance_markings"]
                                if row["case_id"] == case_id])
        policy = policies.get(case_id)
        require(policy is not None and policy["access_policy_version"] == expected_version,
                "ERR_CASE_POLICY_VERSION_MISMATCH", "/case_access_policies")
    for row in domain["case_access_policies"]:
        require(any(case["object_id"] == row["case_id"]
                    for case in domain["operations"]["cases"]),
                "ERR_CASE_POLICY_VERSION_MISMATCH", "/case_access_policies")
    for row in domain["derived_outputs"]:
        require(row["version_seq"] == 1, "ERR_DERIVED_MARKINGS_MISMATCH",
                "/derived_outputs")
        for ref in row["source_refs"]:
            require(ref["object_id"] in objects
                    and ref["version_seq"] <= objects[ref["object_id"]]["version_seq"],
                    "ERR_DERIVED_MARKINGS_MISMATCH", "/derived_outputs")
            require(object_case(domain, ref["object_id"]) == row["case_id"],
                    "ERR_CROSS_CASE_REFERENCE", "/derived_outputs")
        effective = compute_effective_markings(domain, row["source_refs"])
        require(row["effective_markings"] == effective, "ERR_DERIVED_MARKINGS_MISMATCH",
                "/derived_outputs")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == DERIVE
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["output_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/derived_outputs")
        referenced.add(row["created_envelope_id"])
    graph = {}
    for row in domain["lineage_edges"]:
        require(row["from_ref"]["object_id"] in objects
                and row["to_ref"]["object_id"] in objects,
                "ERR_LINEAGE_ENDPOINT_UNKNOWN", "/lineage_edges")
        require(row["from_ref"]["version_seq"] <= objects[row["from_ref"]["object_id"]]["version_seq"]
                and row["to_ref"]["version_seq"] <= objects[row["to_ref"]["object_id"]]["version_seq"],
                "ERR_LINEAGE_ENDPOINT_UNKNOWN", "/lineage_edges")
        require(object_case(domain, row["from_ref"]["object_id"]) == row["case_id"]
                and object_case(domain, row["to_ref"]["object_id"]) == row["case_id"],
                "ERR_CROSS_CASE_REFERENCE", "/lineage_edges")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == EDGE
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["edge_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/lineage_edges")
        referenced.add(row["created_envelope_id"])
        graph.setdefault(row["from_ref"]["object_id"], []).append(row["to_ref"]["object_id"])
    detectable = set()
    def visit(node, stack):
        if node in stack:
            raise ContractFailure("ERR_LINEAGE_CYCLE", "/lineage_edges")
        if node in detectable:
            return
        stack.add(node)
        for nxt in graph.get(node, []):
            visit(nxt, stack)
        stack.discard(node)
        detectable.add(node)
    for node in list(graph):
        visit(node, set())
    latest = latest_markings(domain)
    for row in domain["lineage_edges"]:
        if row["visibility"] == "INTERNAL":
            continue
        endpoints = [latest.get(row["from_ref"]["object_id"]),
                     latest.get(row["to_ref"]["object_id"])]
        require(all(endpoint is not None for endpoint in endpoints),
                "ERR_LINEAGE_VISIBILITY_LEAK", "/lineage_edges")
        if row["visibility"] == "PUBLIC":
            require(all(not endpoint["access_requirements"]
                        and endpoint["export_policy"] == "ALLOWED"
                        for endpoint in endpoints), "ERR_LINEAGE_VISIBILITY_LEAK",
                    "/lineage_edges")
    seen_reviews = set()
    for row in domain["dependency_reviews"]:
        edge = next((item for item in domain["lineage_edges"]
                     if item["edge_id"] == row["edge_id"]), None)
        require(edge is not None and edge["edge_kind"] == "MATERIAL_PREMISE",
                "ERR_DEPENDENCY_EDGE_INVALID", "/dependency_reviews")
        require(row["changed_ref"]["object_id"] in {edge["from_ref"]["object_id"],
                                                    edge["to_ref"]["object_id"]}
                and row["changed_ref"]["version_seq"]
                <= objects[row["changed_ref"]["object_id"]]["version_seq"],
                "ERR_DEPENDENCY_EDGE_INVALID", "/dependency_reviews")
        key = (row["edge_id"], row["changed_ref"]["object_id"])
        require(key not in seen_reviews, "ERR_REVIEW_ALREADY_OPEN", "/dependency_reviews")
        seen_reviews.add(key)
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == REVIEW
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["review_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/dependency_reviews")
        referenced.add(row["created_envelope_id"])
    for envelope in domain["effectiveness_envelopes"]:
        require(envelope["action_type"] in ACTIONS
                and envelope["effect_parameters_sha256"] == digest(envelope["payload_snapshot"]),
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    require(referenced == set(envelopes), "ERR_INVALID_EFFECT_HISTORY",
            "/effectiveness_envelopes")
    return objects


# ---------------------------------------------------------------- diagnostics
def decision_diagnostics(data, objects, target, status):
    cmd, ctx = data["command"], data["server_context"]
    action, payload = cmd["requested_action"], cmd["payload"]
    try:
        require(member_mapping(ctx, ctx["authenticated_principal_id"]) is not None
                and member_mapping(ctx, ctx["authenticated_principal_id"])
                == ctx["authenticated_member_id"], "ERR_SOD_IDENTITY_UNMAPPED",
                "/server_context/authenticated_member_id")
        require(status == "VERIFIED_ACTIVE",
                STATUS_CODES.get(status, "ERR_UNAUTHORIZED_POWER"),
                "/server_context/generated_auth_receipt")
        require(cmd["mandate_claim"]["claimed_power"] == POWERS[action],
                "ERR_UNAUTHORIZED_POWER", "/command/mandate_claim")
        require(ctx["new_envelope_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
                "/server_context/new_envelope_id")
        inp = payload["effectiveness_envelope_input"]
        current = next(m for m in ctx["authority_snapshots"]
                       if m["mandate_id"] == cmd["mandate_claim"]["mandate_id"])
        scope = inp["scope_spec"]
        require(inp["mandate_clause_ref"] == current["clause_reference"]
                and (scope["tenant_node_id"], scope["case_id"])
                == (ctx["tenant_node_id"], ctx["case_id"])
                and scope["effective_from"] <= ctx["evaluated_at"] < scope["effective_until"]
                <= current["valid_until"], "ERR_EFFECTIVENESS_SCOPE_MISMATCH",
                "/command/payload/effectiveness_envelope_input")
        if action == SET:
            _diagnose_set(data, objects, target)
        elif action == RELAX:
            _diagnose_relax(data, target)
        elif action == DERIVE:
            _diagnose_derive(data)
        elif action == EDGE:
            _diagnose_edge(data)
        else:
            _diagnose_review(data, objects)
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


def new_marking_payload(payload):
    return {key: payload[key] for key in
            ("access_requirements", "allowed_purposes", "ai_processing", "export_policy",
             "retention_class")}


def _diagnose_set(data, objects, target):
    payload = data["command"]["payload"]
    require(payload["marking_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/marking_id")
    require(object_case(data["before"]["domain_objects"], target["object_id"])
            == data["server_context"]["case_id"], "ERR_CROSS_CASE_REFERENCE",
            "/command/target_object_id")
    latest = latest_markings(data["before"]["domain_objects"]).get(target["object_id"])
    if latest is not None:
        tightened, relaxed = compare_tightening(latest, payload)
        require(not relaxed and tightened, "ERR_MARKING_UPDATE_NOT_MONOTONIC",
                "/command/payload")


def _diagnose_relax(data, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    latest = latest_markings(data["before"]["domain_objects"]).get(target["object_id"])
    require(latest is not None, "ERR_MARKING_UPDATE_NOT_MONOTONIC",
            "/command/target_object_id")
    tightened, relaxed = compare_tightening(latest, payload)
    require(relaxed and not tightened, "ERR_MARKING_UPDATE_NOT_MONOTONIC",
            "/command/payload")
    review = payload["relaxation_review"]
    require(review["decision"] == "APPROVED"
            and review["reviewer_member_id"] == member_mapping(ctx, review["reviewer_principal_id"]),
            "ERR_MARKING_REVIEW_UNAUTHORIZED", "/command/payload/relaxation_review")
    require(review["reviewed_at"] <= ctx["evaluated_at"],
            "ERR_MARKING_REVIEW_UNAUTHORIZED", "/command/payload/relaxation_review")
    authority = next((row for row in ctx["authority_snapshots"]
                      if row["principal_id"] == review["reviewer_principal_id"]
                      and "REVIEW_MARKING_RELAXATION" in row["powers"]), None)
    require(authority is not None and authority["status"] == "ACTIVE"
            and authority["valid_from"] <= review["reviewed_at"] < authority["valid_until"]
            and (authority["tenant_node_id"], authority["case_id"])
            == (ctx["tenant_node_id"], ctx["case_id"]),
            "ERR_MARKING_REVIEW_UNAUTHORIZED", "/command/payload/relaxation_review")


def _diagnose_derive(data):
    payload, ctx = data["command"]["payload"], data["server_context"]
    domain = data["before"]["domain_objects"]
    require(payload["output_id"] not in object_index(domain), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/output_id")
    for ref in payload["source_refs"]:
        require(object_case(domain, ref["object_id"]) == ctx["case_id"],
                "ERR_CROSS_CASE_REFERENCE", "/command/payload/source_refs")
    compute_effective_markings(domain, payload["source_refs"])


def _diagnose_edge(data):
    payload, ctx = data["command"]["payload"], data["server_context"]
    domain = data["before"]["domain_objects"]
    objects = object_index(domain)
    for ref in (payload["from_ref"], payload["to_ref"]):
        require(ref["object_id"] in objects, "ERR_LINEAGE_ENDPOINT_UNKNOWN",
                "/command/payload")
        require(ref["version_seq"] <= objects[ref["object_id"]]["version_seq"],
                "ERR_LINEAGE_ENDPOINT_UNKNOWN", "/command/payload")
        require(object_case(domain, ref["object_id"]) == ctx["case_id"],
                "ERR_CROSS_CASE_REFERENCE", "/command/payload")
    require(payload["from_ref"]["object_id"] != payload["to_ref"]["object_id"],
            "ERR_LINEAGE_CYCLE", "/command/payload")
    graph = {}
    for row in domain["lineage_edges"]:
        graph.setdefault(row["from_ref"]["object_id"], []).append(row["to_ref"]["object_id"])
    def reachable(node, goal, seen):
        if node == goal:
            return True
        if node in seen:
            return False
        seen.add(node)
        return any(reachable(nxt, goal, seen) for nxt in graph.get(node, []))
    require(not reachable(payload["to_ref"]["object_id"],
                          payload["from_ref"]["object_id"], set()),
            "ERR_LINEAGE_CYCLE", "/command/payload")
    if payload["visibility"] == "INTERNAL":
        return
    latest = latest_markings(domain)
    endpoints = [latest.get(payload["from_ref"]["object_id"]),
                 latest.get(payload["to_ref"]["object_id"])]
    require(all(endpoint is not None for endpoint in endpoints),
            "ERR_LINEAGE_VISIBILITY_LEAK", "/command/payload/visibility")
    if payload["visibility"] == "PUBLIC":
        require(all(not endpoint["access_requirements"]
                    and endpoint["export_policy"] == "ALLOWED"
                    for endpoint in endpoints), "ERR_LINEAGE_VISIBILITY_LEAK",
                "/command/payload/visibility")


def _diagnose_review(data, objects):
    payload, ctx = data["command"]["payload"], data["server_context"]
    domain = data["before"]["domain_objects"]
    require(payload["review_id"] not in object_index(domain), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/review_id")
    edge = next((row for row in domain["lineage_edges"]
                 if row["edge_id"] == payload["edge_id"]), None)
    require(edge is not None and edge["edge_kind"] == "MATERIAL_PREMISE",
            "ERR_DEPENDENCY_EDGE_INVALID", "/command/payload/edge_id")
    require(payload["changed_ref"]["object_id"] in {edge["from_ref"]["object_id"],
                                                    edge["to_ref"]["object_id"]},
            "ERR_DEPENDENCY_EDGE_INVALID", "/command/payload/changed_ref")
    require(payload["due_at"] > ctx["evaluated_at"], "ERR_REVIEW_DUE_INVALID",
            "/command/payload/due_at")
    require(b.known_member(payload["reviewer_member_id"])
            and any(row["member_id"] == payload["reviewer_member_id"]
                    for row in ctx["member_directory"]), "ERR_SOD_IDENTITY_UNMAPPED",
            "/command/payload/reviewer_member_id")


# ---------------------------------------------------------------- expected state
def envelope(data, action, resulting_versions):
    cmd, ctx, payload = data["command"], data["server_context"], data["command"]["payload"]
    inp = payload["effectiveness_envelope_input"]
    claim = cmd["mandate_claim"]
    return {"envelope_id": ctx["new_envelope_id"], "action_type": action,
            "target_object_id": cmd["target_object_id"],
            "evaluated_target_version_seq": cmd["target_expected_version_seq"],
            "resulting_versions": resulting_versions,
            "mandate_ref": {"mandate_id": claim["mandate_id"],
                            "version_hash": claim["expected_version_hash"],
                            "clause_reference": inp["mandate_clause_ref"]},
            "auth_check_receipt": copy.deepcopy(ctx["generated_auth_receipt"]),
            "evidence_refs": copy.deepcopy(inp["evidence_refs"]),
            "scope_spec": copy.deepcopy(inp["scope_spec"]),
            "remedy_spec": copy.deepcopy(inp["remedy_spec"]),
            "effect_parameters_sha256": digest(payload),
            "actor_member_id": ctx["authenticated_member_id"],
            "payload_snapshot": copy.deepcopy(payload)}


def expected_applied_domain(data):
    cmd, ctx, payload = data["command"], data["server_context"], data["command"]["payload"]
    action = cmd["requested_action"]
    domain = copy.deepcopy(data["before"]["domain_objects"])
    case_id = ctx["case_id"]
    if action in {SET, RELAX}:
        target_id = cmd["target_object_id"]
        existing = [row for row in domain["governance_markings"]
                    if row["object_id"] == target_id]
        version = len(existing) + 1
        policy = policy_version(domain, case_id) + 1
        marking = {"marking_id": payload["marking_id"], "object_id": target_id,
                   "case_id": case_id, "tenant_node_id": ctx["tenant_node_id"],
                   "version_seq": version,
                   "object_version_seq": object_index(domain)[target_id]["version_seq"],
                   "access_requirements": copy.deepcopy(payload["access_requirements"]),
                   "allowed_purposes": copy.deepcopy(payload["allowed_purposes"]),
                   "ai_processing": payload["ai_processing"],
                   "export_policy": payload["export_policy"],
                   "retention_class": payload["retention_class"],
                   "cross_case_policy": "SINGLE_CASE_ONLY",
                   "policy_version_seq": policy,
                   "created_command_id": cmd["command_id"],
                   "created_envelope_id": ctx["new_envelope_id"]}
        domain["governance_markings"].append(marking)
        existing_policy = case_policy(domain, case_id)
        if existing_policy is None:
            domain["case_access_policies"].append({
                "case_id": case_id, "tenant_node_id": ctx["tenant_node_id"],
                "access_policy_version": policy,
                "updated_by_command_id": cmd["command_id"]})
        else:
            existing_policy["access_policy_version"] = policy
            existing_policy["updated_by_command_id"] = cmd["command_id"]
        versions = [{"object_id": marking["marking_id"], "version_seq": version}]
    elif action == DERIVE:
        effective = compute_effective_markings(domain, payload["source_refs"])
        output = {"output_id": payload["output_id"], "tenant_node_id": ctx["tenant_node_id"],
                  "case_id": case_id, "version_seq": 1,
                  "output_kind": payload["output_kind"],
                  "source_refs": copy.deepcopy(payload["source_refs"]),
                  "content_sha256": payload["content_sha256"],
                  "effective_markings": effective,
                  "expression_status": payload["expression_status"],
                  "asserted_as_fact": False,
                  "policy_version_seq": policy_version(domain, case_id),
                  "created_command_id": cmd["command_id"],
                  "created_envelope_id": ctx["new_envelope_id"]}
        domain["derived_outputs"].append(output)
        versions = [{"object_id": output["output_id"], "version_seq": 1}]
    elif action == EDGE:
        edge = {"edge_id": payload["edge_id"], "tenant_node_id": ctx["tenant_node_id"],
                "case_id": case_id, "from_ref": copy.deepcopy(payload["from_ref"]),
                "to_ref": copy.deepcopy(payload["to_ref"]),
                "edge_kind": payload["edge_kind"], "visibility": payload["visibility"],
                "created_command_id": cmd["command_id"],
                "created_envelope_id": ctx["new_envelope_id"]}
        domain["lineage_edges"].append(edge)
        versions = [{"object_id": edge["edge_id"], "version_seq": 1}]
    else:
        review = {"review_id": payload["review_id"], "tenant_node_id": ctx["tenant_node_id"],
                  "case_id": case_id, "edge_id": payload["edge_id"],
                  "changed_ref": copy.deepcopy(payload["changed_ref"]),
                  "reason": payload["reason"], "due_at": payload["due_at"],
                  "reviewer_member_id": payload["reviewer_member_id"],
                  "status": "OPEN", "created_command_id": cmd["command_id"],
                  "created_envelope_id": ctx["new_envelope_id"]}
        domain["dependency_reviews"].append(review)
        versions = [{"object_id": review["review_id"], "version_seq": 1}]
    domain["effectiveness_envelopes"].append(envelope(data, action, versions))
    return domain, versions


# ---------------------------------------------------------------- semantics
def validate_semantics(data):
    validate_basis(data)
    before, cmd, ctx, after = data["before"], data["command"], data["server_context"], data["after"]
    out, observation = after["execution_outcome"], ctx["execution_observation"]
    if observation != "NORMAL":
        unknown = observation == "COMMIT_RESPONSE_LOST"
        require(ctx["generated_auth_receipt"] is None and out["auth_check_receipt"] is None
                and out["command_result"] is None and out["command_rejection"] is None
                and out["audit_event"] is None, "ERR_FAULT_CLAIMS_COMPLETED_AUTH",
                "/after/execution_outcome")
        require(out["outcome"] == ("OUTCOME_UNKNOWN" if unknown else "SYSTEM_FAULT")
                and after["domain_objects"] == (None if unknown else before["domain_objects"]),
                "ERR_FAULT_CLAIMS_DOMAIN_STATE", "/after")
        require(out["command_record"] == (None if unknown else before["command_record"])
                and out["command_attempt"] == (None if unknown else before["command_attempt"]),
                "ERR_FAULT_REWRITES_COMMAND_STATE", "/after/execution_outcome")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"],
                    "generation": ctx["generation"], "observed_at": ctx["evaluated_at"],
                    "kind": observation,
                    "recovery_action": "RECONCILE_REQUIRED" if unknown
                    else "RETRY_AFTER_RECONCILIATION",
                    "persistence_claim": "NOT_ASSERTED"}
        require(out["fault_observation"] == expected, "ERR_FAULT_OBSERVATION_MISMATCH",
                "/after/execution_outcome/fault_observation")
        return
    status = current_receipt(data)
    objects, target, _ = validate_basis(data)
    codes = decision_diagnostics(data, objects, target, status)
    expected_status = "REJECTED" if codes else "APPLIED"
    require(out["outcome"] == expected_status, "ERR_DECLARED_OUTCOME_MISMATCH",
            "/after/execution_outcome/outcome")
    if codes:
        domain, versions = before["domain_objects"], []
        require(after["domain_objects"] == domain, "ERR_REJECTED_DOMAIN_MUTATION",
                "/after/domain_objects")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"],
                    "generation": ctx["generation"], "rejection_error_code": codes[0],
                    "diagnostic_codes": codes, "blocking_protective_order_ids": []}
        require(out["command_rejection"] == expected, "ERR_REJECTION_DIAGNOSTIC_MISMATCH",
                "/after/execution_outcome/command_rejection")
    else:
        domain, versions = expected_applied_domain(data)
        require(out["command_rejection"] is None and after["domain_objects"] == domain,
                "ERR_WRITE_SET_VIOLATION", "/after/domain_objects")
        validate_domain(domain)
    require(out["auth_check_receipt"] == ctx["generated_auth_receipt"]
            and out["fault_observation"] is None, "ERR_RECEIPT_BINDING_MISMATCH",
            "/after/execution_outcome")
    retry = codes == ["ERR_MANDATE_SOURCE_UNAVAILABLE"]
    result = out["command_result"]
    require(result is not None, "ERR_COMMAND_RESULT_MISMATCH",
            "/after/execution_outcome/command_result")
    expected = {"result_id": result["result_id"], "command_id": cmd["command_id"],
                "attempt_id": ctx["attempt_id"], "generation": ctx["generation"],
                "status": expected_status,
                "retry_disposition": "NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY" if retry else "FINAL",
                "applied_versions": versions}
    require(result == expected, "ERR_COMMAND_RESULT_MISMATCH",
            "/after/execution_outcome/command_result")
    record, attempt = copy.deepcopy(before["command_record"]), copy.deepcopy(before["command_attempt"])
    record.update(version_seq=record["version_seq"] + 1,
                  status="IN_FLIGHT" if retry else expected_status,
                  result_id=result["result_id"])
    attempt.update(version_seq=attempt["version_seq"] + 1,
                   status="FAILED_RETRYABLE" if retry else expected_status)
    require(out["command_record"] == record and out["command_attempt"] == attempt,
            "ERR_COMMAND_RESULT_BINDING_MISMATCH", "/after/execution_outcome")
    audit = out["audit_event"]
    require(audit is not None, "ERR_AUDIT_BINDING_MISMATCH",
            "/after/execution_outcome/audit_event")
    expected = {"audit_id": audit["audit_id"], "command_id": cmd["command_id"],
                "attempt_id": ctx["attempt_id"], "generation": ctx["generation"],
                "principal_id": ctx["authenticated_principal_id"],
                "action_id": cmd["requested_action"], "outcome": expected_status,
                "checked_at": ctx["evaluated_at"],
                "before_domain_sha256": digest(before["domain_objects"]),
                "after_domain_sha256": digest(domain), "written_versions": versions}
    require(audit == expected, "ERR_AUDIT_BINDING_MISMATCH",
            "/after/execution_outcome/audit_event")


def validate_transition(data):
    registry = check_registry()
    if not registry["is_valid"]:
        return registry
    for validator in (COMMON, VALIDATOR):
        error = b.a.schema_error(validator, data)
        if error:
            return error
    error = validate_payload(data["command"])
    if error:
        return error
    try:
        validate_semantics(data)
    except ContractFailure as failure:
        return failure.report
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
        result = validate_file(path)
        failed |= not result["is_valid"]
        print(json.dumps({"input": str(path), **result}, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
