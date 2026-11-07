"""Slice 4B offline synthetic fixed-baseline branches and AI draft boundaries.

Validation only: never runs a model, never grants effective action, and never
changes a marking or business object.
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
SPEC = ROOT / "specs/aw001_slice4/4b"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

BASELINE, BRANCH, OPEN_SESSION, DRAFT, CONFIRM, REVOKE, FREEZE = (
    "RECORD_BASELINE_SNAPSHOT", "RECORD_PROPOSAL_BRANCH", "OPEN_AI_DRAFT_SESSION",
    "RECORD_AI_DRAFT", "CONFIRM_AI_DRAFT", "REVOKE_AI_SESSION",
    "FREEZE_BRANCH_CANDIDATE")
ACTIONS = (BASELINE, BRANCH, OPEN_SESSION, DRAFT, CONFIRM, REVOKE, FREEZE)
POWERS = {BASELINE: "RECORD_BASELINE_SNAPSHOT", BRANCH: "RECORD_PROPOSAL_BRANCH",
          OPEN_SESSION: "OPEN_AI_DRAFT_SESSION", DRAFT: "RECORD_AI_DRAFT",
          CONFIRM: None, REVOKE: "REVOKE_AI_SESSION", FREEZE: "FREEZE_BRANCH_CANDIDATE"}
PAYLOADS = {BASELINE: "Payload0", BRANCH: "Payload1", OPEN_SESSION: "Payload2",
            DRAFT: "Payload3", CONFIRM: "Payload4", REVOKE: "Payload5", FREEZE: "Payload6"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_BASELINE_AND_BRANCH",
          "CHECK_AI_SESSION_AND_MARKINGS", "CHECK_BRANCH_GATES",
          "CHECK_ALLOWED_WRITE_SET", "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {BASELINE: "CREATE_OR_UPDATE_BASELINE", BRANCH: "CREATE_BRANCH",
          OPEN_SESSION: "CREATE_AI_SESSION", DRAFT: "CREATE_AI_DRAFT",
          CONFIRM: "CONFIRM_DRAFT_ONLY", REVOKE: "REVOKE_SESSION",
          FREEZE: "FREEZE_BRANCH_CANDIDATE"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
AI_FORBIDDEN = "FORBIDDEN"


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
    require(value["contract_version"] == "0.5.0-rc1-slice4b",
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
                and entry["requires_effectiveness_envelope"] is (POWERS[action] is not None)
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


# ---------------------------------------------------------------- domain helpers
def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"],
                                  "kind": "PROTECTIVE_ORDER"}
    return index


def object_case(domain, object_id):
    objects = object_index(domain)
    row = objects.get(object_id)
    return row.get("case_id") if row else None


def latest_markings(domain):
    result = {}
    for row in domain["governance_markings"]:
        result[row["object_id"]] = row
    return result


def latest_baseline(domain, case_id):
    rows = [row for row in domain["baseline_snapshots"] if row["case_id"] == case_id]
    if not rows:
        return None
    return max(rows, key=lambda row: row["version_seq"])


def baseline_by_id(domain, baseline_id):
    return next((row for row in domain["baseline_snapshots"]
                 if row["baseline_id"] == baseline_id), None)


def session_by_id(domain, session_id):
    return next((row for row in domain["ai_sessions"] if row["session_id"] == session_id), None)


def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


# ---------------------------------------------------------------- domain
def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    objects = object_index(domain)
    ids = [row["marking_id"] for row in domain["governance_markings"]]
    ids += [row["baseline_id"] for row in domain["baseline_snapshots"]]
    ids += [row["branch_id"] for row in domain["proposal_branches"]]
    ids += [row["session_id"] for row in domain["ai_sessions"]]
    ids += [row["draft_id"] for row in domain["ai_drafts"]]
    ids += [row["envelope_id"] for row in domain["effectiveness_envelopes"]]
    operation_ids = [row["object_id"] for name in
                     ("cases", "resource_pools", "commitments", "tickets")
                     for row in domain["operations"][name]]
    operation_ids += [row["order_id"] for row in domain["operations"]["protective_orders"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["protection_envelopes"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["effectiveness_envelopes"]]
    require(len(ids) == len(set(ids)) and not set(ids) & set(operation_ids),
            "ERR_DUPLICATE_OBJECT_ID", "/domain_objects")
    envelopes = {row["envelope_id"]: row for row in domain["effectiveness_envelopes"]}
    for row in domain["governance_markings"]:
        require(row["object_id"] in objects
                and row["case_id"] == object_case(domain, row["object_id"]),
                "ERR_MARKINGS_MISSING", "/governance_markings")
    for case_id in {row["case_id"] for row in domain["baseline_snapshots"]}:
        rows = sorted([row for row in domain["baseline_snapshots"]
                       if row["case_id"] == case_id], key=lambda row: row["version_seq"])
        require([row["version_seq"] for row in rows] == list(range(1, len(rows) + 1)),
                "ERR_BASELINE_VERSION_CONFLICT", "/baseline_snapshots")
        require(rows[0]["supersedes_baseline_id"] is None,
                "ERR_BASELINE_VERSION_CONFLICT", "/baseline_snapshots")
        for previous, current in zip(rows, rows[1:]):
            require(current["supersedes_baseline_id"] == previous["baseline_id"],
                    "ERR_BASELINE_VERSION_CONFLICT", "/baseline_snapshots")
    for row in domain["baseline_snapshots"]:
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == BASELINE
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["baseline_id"], "version_seq": row["version_seq"]}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/baseline_snapshots")
    for row in domain["proposal_branches"]:
        baseline = baseline_by_id(domain, row["base_baseline_id"])
        require(baseline is not None and baseline["case_id"] == row["case_id"]
                and baseline["version_seq"] == row["base_baseline_version"],
                "ERR_BRANCH_STALE_BASELINE", "/proposal_branches")
        keys = [parameter["parameter_key"] for parameter in row["parameters"]]
        require(len(keys) == len(set(keys)) and set(keys) <= set(baseline["parameter_allowlist"]),
                "ERR_BRANCH_PARAMETER_NOT_ALLOWED", "/proposal_branches")
        require(row["directly_votable"] == (not row["hypothetical_constraint_changes"]),
                "ERR_BRANCH_CONSTRAINT_CHANGE_NOT_VOTABLE", "/proposal_branches")
        if row["status"] == "FROZEN_CANDIDATE":
            require(row["version_seq"] == 2 and row["freeze_evidence"] is not None
                    and row["frozen_envelope_id"] is not None,
                    "ERR_BRANCH_GATE_INCOMPLETE", "/proposal_branches")
            frozen = envelopes.get(row["frozen_envelope_id"])
            require(frozen is not None and frozen["action_type"] == FREEZE
                    and frozen["target_object_id"] == row["branch_id"]
                    and {"object_id": row["branch_id"], "version_seq": 2}
                    in frozen["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                    "/proposal_branches")
        else:
            require(row["version_seq"] == 1 and row["freeze_evidence"] is None
                    and row["frozen_envelope_id"] is None,
                    "ERR_BRANCH_GATE_INCOMPLETE", "/proposal_branches")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == BRANCH
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["branch_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/proposal_branches")
    latest = latest_markings(domain)
    for row in domain["ai_sessions"]:
        for ref in row["allowed_inputs"]:
            require(ref["object_id"] in objects
                    and ref["version_seq"] <= objects[ref["object_id"]]["version_seq"],
                    "ERR_AI_SESSION_INPUT_UNKNOWN", "/ai_sessions")
            require(object_case(domain, ref["object_id"]) == row["case_id"],
                    "ERR_CROSS_CASE_REFERENCE", "/ai_sessions")
            marking = latest.get(ref["object_id"])
            require(marking is not None, "ERR_AI_SESSION_INPUT_FORBIDDEN", "/ai_sessions")
            require(marking["ai_processing"] != AI_FORBIDDEN
                    and row["purpose"] in marking["allowed_purposes"],
                    "ERR_AI_SESSION_INPUT_FORBIDDEN", "/ai_sessions")
        if row["status"] == "OPEN":
            require(row["revoked_at"] is None and row["revoke_reason"] is None,
                    "ERR_AI_SESSION_REVOKED", "/ai_sessions")
        else:
            require(row["revoked_at"] is not None and row["revoke_reason"] is not None,
                    "ERR_AI_SESSION_REVOKED", "/ai_sessions")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == OPEN_SESSION
                and creation["target_object_id"] == row["case_id"]
                and {"object_id": row["session_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/ai_sessions")
    for row in domain["ai_drafts"]:
        session = session_by_id(domain, row["session_id"])
        require(session is not None and session["case_id"] == row["case_id"],
                "ERR_AI_SESSION_INPUT_UNKNOWN", "/ai_drafts")
        allowed = {(ref["object_id"], ref["version_seq"]) for ref in session["allowed_inputs"]}
        require(all((ref["object_id"], ref["version_seq"]) in allowed
                    for ref in row["source_inputs"]), "ERR_AI_DRAFT_SOURCE_NOT_ALLOWED",
                "/ai_drafts")
        if row["status"] == "DRAFT":
            require(not row["confirmations"], "ERR_AI_DRAFT_NOT_CONFIRMABLE", "/ai_drafts")
        else:
            require(len(row["confirmations"]) == 1
                    and row["confirmations"][0]["decision"]
                    == ("ACCEPTED" if row["status"] == "HUMAN_CONFIRMED" else "REJECTED")
                    and row["confirmations"][0]["confirming_member_id"]
                    == session["requester_member_id"], "ERR_HUMAN_CONFIRMATION_SUBJECT_MISMATCH",
                    "/ai_drafts")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == DRAFT
                and creation["target_object_id"] == session["session_id"]
                and {"object_id": row["draft_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/ai_drafts")
    for envelope in domain["effectiveness_envelopes"]:
        require(envelope["action_type"] in ACTIONS,
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    return objects


# ---------------------------------------------------------------- basis
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
    if POWERS[action] is None:
        require(cmd["mandate_claim"] is None and ctx["generated_auth_receipt"] is None
                and ctx["new_envelope_id"] is None, "ERR_INTAKE_SELF_AUTHORIZATION",
                "/server_context")
        return None
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
    domain = before["domain_objects"]
    target = None
    if cmd["requested_action"] in {BASELINE, BRANCH, OPEN_SESSION}:
        target = next((row for row in domain["operations"]["cases"]
                       if row["object_id"] == cmd["target_object_id"]), None)
        require(target is not None and target["object_id"] == ctx["case_id"],
                "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    elif cmd["requested_action"] == DRAFT:
        target = session_by_id(domain, cmd["target_object_id"])
        require(target is not None, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    elif cmd["requested_action"] == CONFIRM:
        target = next((row for row in domain["ai_drafts"]
                       if row["draft_id"] == cmd["target_object_id"]), None)
        require(target is not None, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    elif cmd["requested_action"] == REVOKE:
        target = session_by_id(domain, cmd["target_object_id"])
        require(target is not None, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    else:
        target = next((row for row in domain["proposal_branches"]
                       if row["branch_id"] == cmd["target_object_id"]), None)
        require(target is not None, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target.get("version_seq", 1),
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
    refs = [member["source_ref"] for member in ctx["member_directory"]]
    if POWERS[cmd["requested_action"]] is not None:
        inp = cmd["payload"]["effectiveness_envelope_input"]
        refs += inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    if cmd["requested_action"] == BASELINE:
        refs.append(cmd["payload"]["source_ref"])
    if cmd["requested_action"] == OPEN_SESSION:
        refs += [cmd["payload"]["task_delegation_ref"],
                 cmd["payload"]["provider_policy_ref"]]
    if cmd["requested_action"] == FREEZE and ctx["branch_gate_snapshot"] is not None:
        refs.append(ctx["branch_gate_snapshot"]["participation_audit_ref"])
    require(all(evidence_ref_resolved(ctx, ref) for ref in refs),
            "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    return objects, target, case


# ---------------------------------------------------------------- diagnostics
def decision_diagnostics(data, objects, target, status):
    cmd, ctx = data["command"], data["server_context"]
    action, payload = cmd["requested_action"], cmd["payload"]
    try:
        require(member_mapping(ctx, ctx["authenticated_principal_id"]) is not None
                and member_mapping(ctx, ctx["authenticated_principal_id"])
                == ctx["authenticated_member_id"], "ERR_SOD_IDENTITY_UNMAPPED",
                "/server_context/authenticated_member_id")
        if POWERS[action] is not None:
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
        elif ctx["generated_auth_receipt"] is not None:
            raise ContractFailure("ERR_INTAKE_SELF_AUTHORIZATION", "/server_context")
        if action == BASELINE:
            _diagnose_baseline(data, ctx)
        elif action == BRANCH:
            _diagnose_branch(data, ctx)
        elif action == OPEN_SESSION:
            _diagnose_open_session(data, ctx)
        elif action == DRAFT:
            _diagnose_draft(data, ctx, target)
        elif action == CONFIRM:
            _diagnose_confirm(data, ctx, target)
        elif action == REVOKE:
            _diagnose_revoke(data, ctx, target)
        else:
            _diagnose_freeze(data, ctx, target)
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


def _diagnose_baseline(data, ctx):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(all(row["baseline_id"] != payload["baseline_id"]
                for row in domain["baseline_snapshots"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/baseline_id")
    latest = latest_baseline(domain, ctx["case_id"])
    if payload["supersedes_baseline_id"] is None:
        require(latest is None and not domain["baseline_snapshots"],
                "ERR_BASELINE_VERSION_CONFLICT", "/command/payload/supersedes_baseline_id")
    else:
        require(latest is not None
                and payload["supersedes_baseline_id"] == latest["baseline_id"],
                "ERR_BASELINE_VERSION_CONFLICT", "/command/payload/supersedes_baseline_id")


def _diagnose_branch(data, ctx):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(all(row["branch_id"] != payload["branch_id"]
                for row in domain["proposal_branches"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/branch_id")
    baseline = baseline_by_id(domain, payload["base_baseline_id"])
    require(baseline is not None and baseline["case_id"] == ctx["case_id"]
            and baseline["version_seq"] == payload["base_baseline_version"],
            "ERR_BRANCH_STALE_BASELINE", "/command/payload/base_baseline_version")
    keys = [parameter["parameter_key"] for parameter in payload["parameters"]]
    require(len(keys) == len(set(keys))
            and set(keys) <= set(baseline["parameter_allowlist"]),
            "ERR_BRANCH_PARAMETER_NOT_ALLOWED", "/command/payload/parameters")


def _diagnose_open_session(data, ctx):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(all(row["session_id"] != payload["session_id"]
                for row in domain["ai_sessions"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/session_id")
    latest = latest_markings(domain)
    for ref in payload["allowed_inputs"]:
        require(ref["object_id"] in object_index(domain)
                and ref["version_seq"] <= object_index(domain)[ref["object_id"]]["version_seq"],
                "ERR_AI_SESSION_INPUT_UNKNOWN", "/command/payload/allowed_inputs")
        require(object_case(domain, ref["object_id"]) == ctx["case_id"],
                "ERR_CROSS_CASE_REFERENCE", "/command/payload/allowed_inputs")
        marking = latest.get(ref["object_id"])
        require(marking is not None, "ERR_AI_SESSION_INPUT_FORBIDDEN",
                "/command/payload/allowed_inputs")
        require(marking["ai_processing"] != AI_FORBIDDEN
                and payload["purpose"] in marking["allowed_purposes"],
                "ERR_AI_SESSION_INPUT_FORBIDDEN", "/command/payload/allowed_inputs")


def _diagnose_draft(data, ctx, session):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(session["status"] == "OPEN", "ERR_AI_SESSION_REVOKED",
            "/domain_objects/ai_sessions")
    require(all(row["draft_id"] != payload["draft_id"]
                for row in domain["ai_drafts"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/draft_id")
    allowed = {(ref["object_id"], ref["version_seq"]) for ref in session["allowed_inputs"]}
    require(all((ref["object_id"], ref["version_seq"]) in allowed
                for ref in payload["source_inputs"]), "ERR_AI_DRAFT_SOURCE_NOT_ALLOWED",
            "/command/payload/source_inputs")


def _diagnose_confirm(data, ctx, draft):
    payload = data["command"]["payload"]
    session = session_by_id(data["before"]["domain_objects"], draft["session_id"])
    require(draft["status"] == "DRAFT", "ERR_AI_DRAFT_NOT_CONFIRMABLE",
            "/domain_objects/ai_drafts")
    require(ctx["authenticated_member_id"] == session["requester_member_id"],
            "ERR_HUMAN_CONFIRMATION_SUBJECT_MISMATCH",
            "/server_context/authenticated_member_id")
    require(all(row["confirmation_id"] != payload["confirmation_id"]
                for row in draft["confirmations"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/confirmation_id")


def _diagnose_revoke(data, ctx, session):
    require(session["status"] == "OPEN", "ERR_AI_SESSION_REVOKED",
            "/domain_objects/ai_sessions")


def _diagnose_freeze(data, ctx, branch):
    domain = data["before"]["domain_objects"]
    latest = latest_baseline(domain, ctx["case_id"])
    require(branch["status"] == "DRAFT", "ERR_BRANCH_GATE_INCOMPLETE",
            "/domain_objects/proposal_branches")
    require(latest is not None and branch["base_baseline_version"] == latest["version_seq"],
            "ERR_BRANCH_STALE_BASELINE", "/domain_objects/proposal_branches")
    require(not branch["hypothetical_constraint_changes"] and branch["directly_votable"],
            "ERR_BRANCH_CONSTRAINT_CHANGE_NOT_VOTABLE", "/domain_objects/proposal_branches")
    gates = ctx["branch_gate_snapshot"]
    require(gates is not None, "ERR_BRANCH_GATE_INCOMPLETE", "/server_context")
    require(gates["participation_status"] == "MET"
            and gates["constraint_check_status"] == "PASSED"
            and gates["authorization_check_status"] == "VERIFIED_ACTIVE",
            "ERR_BRANCH_GATE_INCOMPLETE", "/server_context/branch_gate_snapshot")


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
    if action == BASELINE:
        latest = latest_baseline(domain, case_id)
        version = (latest["version_seq"] + 1) if latest else 1
        row = {"baseline_id": payload["baseline_id"], "tenant_node_id": ctx["tenant_node_id"],
               "case_id": case_id, "version_seq": version,
               "supersedes_baseline_id": payload["supersedes_baseline_id"],
               "constraints": copy.deepcopy(payload["constraints"]),
               "parameter_allowlist": copy.deepcopy(payload["parameter_allowlist"]),
               "source_ref": copy.deepcopy(payload["source_ref"]),
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["baseline_snapshots"].append(row)
        versions = [{"object_id": row["baseline_id"], "version_seq": version}]
    elif action == BRANCH:
        row = {"branch_id": payload["branch_id"], "tenant_node_id": ctx["tenant_node_id"],
               "case_id": case_id, "version_seq": 1,
               "base_baseline_id": payload["base_baseline_id"],
               "base_baseline_version": payload["base_baseline_version"],
               "option_code": payload["option_code"],
               "parameters": copy.deepcopy(payload["parameters"]),
               "hypothetical_constraint_changes": copy.deepcopy(
                   payload["hypothetical_constraint_changes"]),
               "directly_votable": not payload["hypothetical_constraint_changes"],
               "status": "DRAFT", "model_ref": payload["model_ref"],
               "input_refs": copy.deepcopy(payload["input_refs"]),
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"],
               "freeze_evidence": None, "frozen_envelope_id": None}
        domain["proposal_branches"].append(row)
        versions = [{"object_id": row["branch_id"], "version_seq": 1}]
    elif action == OPEN_SESSION:
        row = {"session_id": payload["session_id"], "tenant_node_id": ctx["tenant_node_id"],
               "case_id": case_id,
               "requester_principal_id": ctx["authenticated_principal_id"],
               "requester_member_id": ctx["authenticated_member_id"],
               "purpose": payload["purpose"],
               "task_delegation_ref": copy.deepcopy(payload["task_delegation_ref"]),
               "model_ref": payload["model_ref"],
               "provider_policy_ref": copy.deepcopy(payload["provider_policy_ref"]),
               "allowed_inputs": copy.deepcopy(payload["allowed_inputs"]),
               "status": "OPEN", "opened_at": ctx["evaluated_at"],
               "revoked_at": None, "revoke_reason": None,
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["ai_sessions"].append(row)
        versions = [{"object_id": row["session_id"], "version_seq": 1}]
    elif action == DRAFT:
        session = session_by_id(domain, cmd["target_object_id"])
        row = {"draft_id": payload["draft_id"], "session_id": session["session_id"],
               "tenant_node_id": ctx["tenant_node_id"], "case_id": case_id,
               "output_kind": payload["output_kind"],
               "content_sha256": payload["content_sha256"],
               "source_inputs": copy.deepcopy(payload["source_inputs"]),
               "status": "DRAFT", "confirmations": [],
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["ai_drafts"].append(row)
        versions = [{"object_id": row["draft_id"], "version_seq": 1}]
    elif action == CONFIRM:
        draft = next(row for row in domain["ai_drafts"]
                     if row["draft_id"] == cmd["target_object_id"])
        draft["confirmations"].append({
            "confirmation_id": payload["confirmation_id"],
            "confirming_member_id": ctx["authenticated_member_id"],
            "decision": payload["decision"], "confirmed_at": ctx["evaluated_at"],
            "command_id": cmd["command_id"]})
        draft["status"] = ("HUMAN_CONFIRMED" if payload["decision"] == "ACCEPTED"
                           else "HUMAN_REJECTED")
        versions = [{"object_id": draft["draft_id"], "version_seq": 1}]
    elif action == REVOKE:
        session = session_by_id(domain, cmd["target_object_id"])
        session.update(status="REVOKED", revoked_at=ctx["evaluated_at"],
                       revoke_reason=payload["reason"])
        versions = [{"object_id": session["session_id"], "version_seq": 1}]
    else:
        branch = next(row for row in domain["proposal_branches"]
                      if row["branch_id"] == cmd["target_object_id"])
        gates = ctx["branch_gate_snapshot"]
        branch.update(version_seq=2, status="FROZEN_CANDIDATE",
                      freeze_evidence={
                          "participation_audit_ref": copy.deepcopy(
                              gates["participation_audit_ref"]),
                          "constraint_check_status": "PASSED",
                          "authorization_check_status": "VERIFIED_ACTIVE",
                          "frozen_at": ctx["evaluated_at"]},
                      frozen_envelope_id=ctx["new_envelope_id"])
        versions = [{"object_id": branch["branch_id"], "version_seq": 2}]
    if POWERS[action] is not None:
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
