"""Slice 2B offline synthetic commitment/ticket contracts; never executes resources."""

import argparse
import copy
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import validate_aw001_slice2 as a
from scripts.validate_case_packet import FORMATS, load_json

report, require, digest, ContractFailure = a.report, a.require, a.digest, a.ContractFailure
SPEC = ROOT / "specs/aw001_slice2/2b"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON_VALIDATOR = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN_VALIDATOR = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)
APPROVE, CANCEL, AUTHORIZE, HANDOFF, LOG, VERIFY = (
    "APPROVE_COMMITMENT", "CANCEL_INTERNAL_TICKET", "AUTHORIZE_TICKET_HANDOFF",
    "RECORD_TICKET_HANDOFF", "LOG_EXTERNAL_RECEIPT", "VERIFY_EXECUTION_RECEIPT",
)
ACTIONS = (APPROVE, CANCEL, AUTHORIZE, HANDOFF, LOG, VERIFY)
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION", "CHECK_FOUR_TUPLE_COMPLETE", "CHECK_AUTHORITY_SNAPSHOT", "CHECK_PROTECTION_SCOPE_AND_TIME", "CHECK_RESOURCE_LEDGER", "CHECK_SOD_AND_DEPENDENCIES", "CHECK_ALLOWED_WRITE_SET", "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITE_POLICIES = dict(zip(ACTIONS, ["UPDATE_POOL_CREATE_COMMITMENT_ALL_TICKETS", "CANCEL_TICKET_ONLY", "APPEND_HANDOFF_AUTHORIZATION", "RECORD_HANDOFF_ONLY", "APPEND_RECEIPT_ONLY", "APPEND_VERIFICATION_UPDATE_VERIFIED_DELTA"]))
COLLECTION_KEYS = {"cases": "object_id", "resource_pools": "object_id", "commitments": "object_id", "tickets": "object_id", "execution_verifications": "verification_id", "protective_orders": "order_id", "protection_envelopes": "envelope_id", "effectiveness_envelopes": "envelope_id"}


def check_registry(path=DEFAULT_REGISTRY):
    try:
        value = load_json(path)
    except (ValueError, OSError):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    expected_keys = {"contract_version", "global_lock_order", "resource_pool_lock_rule", "actions"}
    if not isinstance(value, dict) or set(value) != expected_keys or value["contract_version"] != "0.3.0-rc1-slice2b" or not isinstance(value["actions"], dict) or set(value["actions"]) != set(ACTIONS):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if value["global_lock_order"] != ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder", "FulfillmentTicket"] or value["resource_pool_lock_rule"] != "LEVEL_2_STABLE_ID_ORDER_WITH_CASE_AND_COMMITMENT":
        return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/global_lock_order")
    for action in ACTIONS:
        entry = value["actions"][action]
        if not isinstance(entry, dict) or set(entry) != {"required_power", "requires_effectiveness_envelope", "required_validators", "lock_levels", "write_policy"} or entry["required_power"] != action or entry["requires_effectiveness_envelope"] is not True or entry["write_policy"] != WRITE_POLICIES[action]:
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/actions/" + action)
        if not isinstance(entry["required_validators"], list) or any(c not in entry["required_validators"] for c in CHECKS):
            return report("REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING", "/actions/" + action)
        if entry["required_validators"] != CHECKS:
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/actions/" + action)
        if entry["lock_levels"] != [0, 1, 2, 3, 4] or any(type(v) is not int for v in entry["lock_levels"]):
            return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/actions/" + action)
    return report("REGISTRY_LOADER")


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


def known_member(member_id):
    return member_id is not None and member_id != "UNMAPPED"


def domain_index(domain):
    index, seen = {}, set()
    for collection, key in COLLECTION_KEYS.items():
        index[collection] = {}
        for row in domain[collection]:
            require(row[key] not in seen, "ERR_DUPLICATE_OBJECT_ID", "/domain_objects/" + collection)
            seen.add(row[key]); index[collection][row[key]] = row
    return index


def protection_projection(domain):
    """Only case/commitment/ticket ancestry; never coerce an unsupported power."""
    resources = []
    for collection in ["cases", "commitments", "tickets"]:
        for row in domain[collection]:
            resources.append({key: row[key] for key in ["object_id", "kind", "tenant_node_id", "case_id", "parent_object_id", "version_seq"]})
    return {"resources": resources, "protective_orders": domain["protective_orders"], "effectiveness_envelopes": domain["protection_envelopes"]}


def internal_ticket_reserved_minor(domain, commitment_id):
    """A derived allocation view, not cash available to the public resource pool."""
    return sum(t["allocated_minor"] for t in domain["tickets"] if t["parent_object_id"] == commitment_id and t["ticket_status"] != "CANCELLED")


def validate_domain(domain):
    idx = domain_index(domain)
    a.validate_domain(protection_projection(domain))
    cases, pools, commitments, tickets, verifications, envs = [idx[k] for k in ["cases", "resource_pools", "commitments", "tickets", "execution_verifications", "effectiveness_envelopes"]]
    objects = {**cases, **pools, **commitments, **tickets, **verifications}
    for env in envs.values():
        receipt, scope = env["auth_check_receipt"], env["scope_spec"]
        target = objects.get(env["target_object_id"])
        require(target is not None and (target["tenant_node_id"], target["case_id"]) == (scope["tenant_node_id"], scope["case_id"])
                and scope["effective_from"] <= receipt["checked_at"] < scope["effective_until"]
                and receipt["verification_status"] == "VERIFIED_ACTIVE" and receipt["requested_power"] == env["action_type"]
                and receipt["target_object_id"] == env["target_object_id"] and receipt["evaluated_target_version_seq"] == env["evaluated_target_version_seq"]
                and receipt["mandate_id"] == env["mandate_ref"]["mandate_id"] and receipt["evaluated_version_hash"] == env["mandate_ref"]["version_hash"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/effectiveness_envelopes")
        result_ids = [v["object_id"] for v in env["resulting_versions"]]
        require(len(result_ids) == len(set(result_ids)) and all(v["object_id"] in objects and v["version_seq"] <= objects[v["object_id"]]["version_seq"] for v in env["resulting_versions"]), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/effectiveness_envelopes")
    def history(env_id, action, target_id, resulting_version=None):
        env = envs.get(env_id)
        require(env is not None and env["action_type"] == action and env["target_object_id"] == target_id, "ERR_INVALID_EFFECT_HISTORY", "/domain_objects")
        if resulting_version is not None:
            require({"object_id": target_id, "version_seq": resulting_version} in env["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects")
        return env
    for pool in pools.values():
        case = cases.get(pool["case_id"])
        require(case is not None and case["tenant_node_id"] == pool["tenant_node_id"], "ERR_INVALID_OBJECT_HIERARCHY", "/domain_objects/resource_pools")
        total = sum(c["total_obligated_minor"] for c in commitments.values() if c["budget_pool_id"] == pool["object_id"])
        require(total == pool["committed_obligations_minor"] <= pool["total_capacity_minor"], "ERR_RESOURCE_LEDGER_MISMATCH", "/domain_objects/resource_pools")
    for c in commitments.values():
        pool = pools.get(c["budget_pool_id"])
        require(pool is not None and (pool["tenant_node_id"], pool["case_id"]) == (c["tenant_node_id"], c["case_id"]) and pool["resource_spec"] == c["resource_spec"], "ERR_RESOURCE_SCOPE_MISMATCH", "/domain_objects/commitments")
        phases = c["phases"]
        require([p["phase_seq"] for p in phases] == list(range(1, len(phases) + 1)) and sum(p["allocated_minor"] for p in phases) == c["total_obligated_minor"], "ERR_PHASE_LEDGER_MISMATCH", "/domain_objects/commitments")
        c_tickets = [t for t in tickets.values() if t["parent_object_id"] == c["object_id"]]
        require(len({p["ticket_id"] for p in phases}) == len(phases) and {p["ticket_id"] for p in phases} == {t["object_id"] for t in c_tickets}, "ERR_PHASE_LEDGER_MISMATCH", "/domain_objects/commitments")
        require(sum(t["cumulative_verified_minor"] for t in c_tickets) == c["verified_fulfilled_minor"] <= c["total_obligated_minor"], "ERR_RESOURCE_LEDGER_MISMATCH", "/domain_objects/commitments")
        expected = "COMPLETED" if c["verified_fulfilled_minor"] == c["total_obligated_minor"] else ("PARTIALLY_FULFILLED" if c["verified_fulfilled_minor"] else "ACTIVE")
        require(c["commitment_status"] == expected, "ERR_RESOURCE_LEDGER_MISMATCH", "/domain_objects/commitments")
        env = history(c["creation_envelope_id"], APPROVE, c["case_id"])
        require({"object_id": c["object_id"], "version_seq": 1} in env["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/commitments")
    receipt_ids, external_keys, authorization_ids = set(), set(), set()
    for t in tickets.values():
        c = commitments.get(t["parent_object_id"])
        require(c is not None, "ERR_INVALID_OBJECT_HIERARCHY", "/domain_objects/tickets")
        phase = next((p for p in c["phases"] if p["ticket_id"] == t["object_id"]), None)
        require(phase is not None and phase["phase_seq"] == t["phase_seq"] and phase["allocated_minor"] == t["allocated_minor"] and c["resource_spec"] == t["resource_spec"], "ERR_PHASE_LEDGER_MISMATCH", "/domain_objects/tickets")
        require(t["creation_envelope_id"] == c["creation_envelope_id"] and {"object_id": t["object_id"], "version_seq": 1} in envs[c["creation_envelope_id"]]["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/tickets")
        auth = t["handoff_authorization"]
        for permission in t["handoff_authorization_history"] + ([auth] if auth else []):
            require(permission["authorization_id"] not in authorization_ids, "ERR_DUPLICATE_OBJECT_ID", "/domain_objects/tickets")
            authorization_ids.add(permission["authorization_id"])
            env = history(permission["envelope_id"], AUTHORIZE, t["object_id"], permission["authorized_ticket_version_seq"])
            require(permission["origin_mandate_id"] == env["mandate_ref"]["mandate_id"] and permission["origin_mandate_version_hash"] == env["mandate_ref"]["version_hash"]
                    and permission["authorizer_principal_id"] == env["auth_check_receipt"]["authenticated_principal_id"]
                    and env["auth_check_receipt"]["checked_at"] < permission["handoff_expires_at"] <= env["scope_spec"]["effective_until"]
                    and permission["authorized_ticket_version_seq"] <= t["version_seq"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/tickets")
        state = t["ticket_status"]
        require(state not in {"READY_FOR_HANDOFF", "HANDED_OFF_TO_EXTERNAL", "EXTERNAL_RECEIPT_LOGGED"} or auth is not None, "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
        if state in {"QUEUED_INTERNAL", "BLOCKED"}:
            require(auth is None and not t["handoff_authorization_history"], "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
        if state in {"HANDED_OFF_TO_EXTERNAL", "EXTERNAL_RECEIPT_LOGGED"}:
            handoff = t["handoff_record"]
            require(handoff is not None and handoff["authorization_id"] == auth["authorization_id"], "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
            env = history(handoff["envelope_id"], HANDOFF, t["object_id"], auth["authorized_ticket_version_seq"] + 1)
            require(handoff["recorded_at"] == env["auth_check_receipt"]["checked_at"] and handoff["operator_principal_id"] == env["auth_check_receipt"]["authenticated_principal_id"], "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
        else:
            require(t["handoff_record"] is None, "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
        require((state == "EXTERNAL_RECEIPT_LOGGED") == bool(t["receipt_entries"]), "ERR_INVALID_TICKET_HISTORY", "/domain_objects/tickets")
        entries = {}
        for receipt in t["receipt_entries"]:
            key = (c["object_id"], receipt["external_reference_number"])
            require(receipt["receipt_id"] not in receipt_ids and key not in external_keys, "ERR_DUPLICATE_RECEIPT", "/domain_objects/tickets")
            receipt_ids.add(receipt["receipt_id"]); external_keys.add(key); entries[receipt["receipt_id"]] = receipt
            require(receipt["claimed_amount_minor"] <= t["allocated_minor"], "ERR_RESOURCE_LEDGER_MISMATCH", "/domain_objects/tickets")
            history(receipt["envelope_id"], LOG, t["object_id"], receipt["logged_at_ticket_version_seq"])
        rows = [v for v in verifications.values() if v["ticket_id"] == t["object_id"]]
        require(set(t["verification_ids"]) == {v["verification_id"] for v in rows}, "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/tickets")
        successful = set()
        for v in rows:
            receipt = entries.get(v["target_receipt_id"])
            require(receipt is not None and (v["tenant_node_id"], v["case_id"]) == (t["tenant_node_id"], t["case_id"])
                    and v["target_receipt_logged_version_seq"] == receipt["logged_at_ticket_version_seq"] and v["receipt_attachment_sha256"] == receipt["attachment_sha256"]
                    and receipt["logged_at_ticket_version_seq"] <= v["ticket_evaluated_version_seq"] < t["version_seq"] and v["version_seq"] == 1, "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/execution_verifications")
            env = history(v["envelope_id"], VERIFY, t["object_id"], v["ticket_evaluated_version_seq"] + 1)
            require(env["evaluated_target_version_seq"] == v["ticket_evaluated_version_seq"] and {"object_id": v["verification_id"], "version_seq": 1} in env["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/execution_verifications")
            if v["verification_outcome"] == "VERIFIED_FULFILLED":
                require(v["target_receipt_id"] not in successful and 0 < v["verified_delta_minor"] <= receipt["claimed_amount_minor"], "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/execution_verifications")
                successful.add(v["target_receipt_id"])
            else:
                require(v["verified_delta_minor"] == 0, "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/execution_verifications")
        require(sum(v["verified_delta_minor"] for v in rows) == t["cumulative_verified_minor"] <= t["allocated_minor"], "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/tickets")
    require(all(v["ticket_id"] in tickets for v in verifications.values()), "ERR_VERIFICATION_LEDGER_MISMATCH", "/domain_objects/execution_verifications")
    return idx


def evaluate_protective_orders(domain, target_id, power, evaluated_at):
    require(DOMAIN_VALIDATOR.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    require(isinstance(power, str) and power in SCHEMA["$defs"]["Power"]["enum"] and isinstance(target_id, str), "ERR_INVALID_GATE_ARGUMENT", "/power")
    require(isinstance(evaluated_at, str) and FORMATS.conforms(evaluated_at, "date-time"), "ERR_INVALID_GATE_ARGUMENT", "/evaluated_at")
    validate_domain(domain)
    projection = protection_projection(domain)
    resources, orders, _ = a.domain_index(projection)
    a.ancestors(target_id, resources, orders)
    return sorted(oid for oid, order in orders.items() if a.active(order, evaluated_at) and power in order["blocked_powers"] and a.covers(order, target_id, resources, orders))


def validate_basis(data):
    before, cmd, ctx = data["before"], data["command"], data["server_context"]
    require(ctx["access_granted"], "ERR_CONTEXT_ACCESS_DENIED", "/server_context/access_granted")
    idx = validate_domain(before["domain_objects"])
    objects = {**idx["cases"], **idx["commitments"], **idx["tickets"], **idx["resource_pools"]}
    target = objects.get(cmd["target_object_id"])
    require(target is not None and (target["tenant_node_id"], target["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"]), "ERR_CONTEXT_SCOPE_MISMATCH", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    require(target["object_id"] in (idx["cases"] if cmd["requested_action"] == APPROVE else idx["tickets"]), "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    if cmd["requested_action"] == APPROVE:
        deps = [cmd["payload"]["new_commitment"]["budget_pool_id"]]
        require(deps[0] in idx["resource_pools"], "ERR_INVALID_RESOURCE_POOL_REFERENCE", "/command/payload/new_commitment/budget_pool_id")
    else:
        deps = [target["case_id"], target["parent_object_id"]]
    require(set(v["object_id"] for v in cmd["dependency_versions"]) == set(deps) and len(cmd["dependency_versions"]) == len(deps)
            and all(v["object_id"] in objects and v["version_seq"] == objects[v["object_id"]]["version_seq"] for v in cmd["dependency_versions"]), "ERR_DEPENDENCY_VERSION_MISMATCH", "/command/dependency_versions")
    record, attempt = before["command_record"], before["command_attempt"]
    require(record["status"] == "IN_FLIGHT" and record["result_id"] is None and attempt["status"] == "IN_FLIGHT", "ERR_TERMINAL_COMMAND_REEXECUTION", "/before/command_record")
    expected = {"command_id": cmd["command_id"], "tenant_node_id": ctx["tenant_node_id"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "idempotency_key": cmd["idempotency_key"], "request_sha256": digest(cmd), "current_attempt_id": ctx["attempt_id"], "generation": ctx["generation"]}
    require(all(record[k] == v for k, v in expected.items()), "ERR_COMMAND_BINDING_MISMATCH", "/before/command_record")
    require(attempt["attempt_id"] == ctx["attempt_id"] and attempt["command_id"] == cmd["command_id"] and attempt["generation"] == ctx["generation"], "ERR_ATTEMPT_GENERATION_MISMATCH", "/before/command_attempt")
    ids = {i for group in idx.values() for i in group}
    require(ctx["new_envelope_id"] not in ids, "ERR_DUPLICATE_OBJECT_ID", "/server_context/new_envelope_id")
    evidence = {(e["evidence_id"], e["revision"]): e for e in ctx["evidence_snapshots"]}
    require(len(evidence) == len(ctx["evidence_snapshots"]), "ERR_DUPLICATE_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    inp = cmd["payload"]["effectiveness_envelope_input"]
    for e in inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]] + [m["evidence_ref"] for m in ctx["metric_evaluations"]]:
        require(evidence.get((e["evidence_id"], e["revision"])) == e, "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    require(len({m["principal_id"] for m in ctx["member_mappings"]}) == len(ctx["member_mappings"]), "ERR_MEMBER_MAPPING_CONFLICT", "/server_context/member_mappings")
    require(len({m["mandate_id"] for m in ctx["authority_snapshots"]}) == len(ctx["authority_snapshots"]), "ERR_AUTHORITY_SNAPSHOT_CONFLICT", "/server_context/authority_snapshots")
    require(len({m["metric_id"] for m in ctx["metric_evaluations"]}) == len(ctx["metric_evaluations"]), "ERR_METRIC_SNAPSHOT_CONFLICT", "/server_context/metric_evaluations")
    return idx, target


def metric_compliant(ctx, c, phase, case):
    if phase["required_metric_id"] is None:
        return True
    m = next((m for m in ctx["metric_evaluations"] if m["metric_id"] == phase["required_metric_id"]), None)
    return bool(m and (m["tenant_node_id"], m["case_id"], m["commitment_id"], m["phase_seq"]) == (c["tenant_node_id"], c["case_id"], c["object_id"], phase["phase_seq"])
                and m["commitment_terms_version_seq"] == c["terms_version_seq"] and m["case_rule_version_seq"] == case["rule_version_seq"]
                and m["status"] == "COMPLIANT" and m["valid_from"] <= ctx["evaluated_at"] < m["valid_until"])


def decision_diagnostics(data, idx, target):
    cmd, ctx, domain = data["command"], data["server_context"], data["before"]["domain_objects"]
    action, payload, now = cmd["requested_action"], cmd["payload"], ctx["evaluated_at"]
    case = idx["cases"][ctx["case_id"]]
    c = None if action == APPROVE else idx["commitments"][target["parent_object_id"]]
    pool_id = payload["new_commitment"]["budget_pool_id"] if c is None else c["budget_pool_id"]
    pool = idx["resource_pools"][pool_id]
    projection = protection_projection(domain); resources, orders, _ = a.domain_index(projection)
    matched = evaluate_protective_orders(domain, target["object_id"], action, now)
    status = a.authority_status(cmd, ctx, resources, orders)
    receipt = ctx["generated_auth_receipt"]
    require(receipt is not None, "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
    bindings = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "idempotency_key": cmd["idempotency_key"], "authenticated_principal_id": ctx["authenticated_principal_id"], "mandate_id": cmd["mandate_claim"]["mandate_id"], "evaluated_version_hash": cmd["mandate_claim"]["expected_version_hash"], "requested_power": action, "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "checked_at": now, "authority_source_id": ctx["authority_source_id"], "domain_sha256": digest(domain), "context_sha256": context_digest(ctx), "verification_status": status}
    require(all(receipt[k] == v for k, v in bindings.items()), "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
    codes = ["ERR_BLOCKED_BY_PROTECTIVE_ORDER"] if matched else []
    status_codes = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE", "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED", "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED", "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER", "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH", "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
    if status != "VERIFIED_ACTIVE": codes.append(status_codes[status])
    amount = payload["new_commitment"]["total_obligated_minor"] if c is None else (payload["verified_delta_minor"] if action == VERIFY else target["allocated_minor"])
    scope = payload["effectiveness_envelope_input"]["scope_spec"]
    if ((scope["tenant_node_id"], scope["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]) or not scope["effective_from"] <= now < scope["effective_until"]
            or scope["resource_spec"] != pool["resource_spec"] or amount > scope["max_amount_minor"]):
        codes.append("ERR_EFFECTIVENESS_SCOPE_MISMATCH")
    mandate = ctx["authority_snapshot"]
    if status == "VERIFIED_ACTIVE" and (pool_id not in mandate["allowed_pool_ids"] or mandate["resource_spec"] != pool["resource_spec"] or amount > mandate["max_amount_minor"]):
        codes.append("ERR_MANDATE_SCOPE_MISMATCH")
    if (pool["tenant_node_id"], pool["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]): codes.append("ERR_RESOURCE_SCOPE_MISMATCH")
    if action == APPROVE and pool["committed_obligations_minor"] + amount > pool["total_capacity_minor"]: codes.append("ERR_RESOURCE_POOL_EXHAUSTED")
    mapping = next((m for m in ctx["member_mappings"] if m["principal_id"] == ctx["authenticated_principal_id"]), None)
    member = ctx["authenticated_member_id"]
    if not mapping or mapping["status"] != "VERIFIED" or not known_member(member) or mapping["member_id"] != member:
        codes.append("ERR_SOD_IDENTITY_UNMAPPED")
    elif action == APPROVE:
        if not known_member(case["proposer_member_id"]): codes.append("ERR_SOD_IDENTITY_UNMAPPED")
        elif member == case["proposer_member_id"]: codes.append("ERR_SOD_CONFLICT")
    elif action == VERIFY and target["handoff_authorization"] and target["handoff_record"]:
        responsible = [target["handoff_authorization"]["authorizer_member_id"], target["handoff_record"]["operator_member_id"]]
        if any(not known_member(m) for m in responsible): codes.append("ERR_SOD_IDENTITY_UNMAPPED")
        elif member in responsible: codes.append("ERR_SOD_CONFLICT")
        if case["template_sod_policy"]["exclude_commitment_executors_from_verification"]:
            if any(not known_member(m) for m in c["executor_member_ids"]): codes.append("ERR_SOD_IDENTITY_UNMAPPED")
            elif member in c["executor_member_ids"]: codes.append("ERR_SOD_TEMPLATE_EXECUTOR_CONFLICT")
    if action == APPROVE:
        new = payload["new_commitment"]; phases = new["phases"]
        ids = [new["commitment_id"]] + [p["ticket_id"] for p in phases]
        existing = {ident for group in idx.values() for ident in group} | {ctx["new_envelope_id"]}
        if len(ids) != len(set(ids)) or set(ids) & existing: codes.append("ERR_NEW_OBJECT_ID_CONFLICT")
        if new["resource_spec"] != pool["resource_spec"]: codes.append("ERR_RESOURCE_SCOPE_MISMATCH")
        if [p["phase_seq"] for p in phases] != list(range(1, len(phases) + 1)) or sum(p["allocated_minor"] for p in phases) != amount: codes.append("ERR_PHASE_LEDGER_MISMATCH")
        if len(new["ticket_executor_member_ids"]) != len(phases) or {m["ticket_id"] for m in new["ticket_executor_member_ids"]} != {p["ticket_id"] for p in phases}: codes.append("ERR_PHASE_LEDGER_MISMATCH")
    else:
        state = target["ticket_status"]
        states = {CANCEL: {"QUEUED_INTERNAL", "READY_FOR_HANDOFF"}, AUTHORIZE: {"QUEUED_INTERNAL", "READY_FOR_HANDOFF"}, HANDOFF: {"READY_FOR_HANDOFF"}, LOG: {"HANDED_OFF_TO_EXTERNAL", "EXTERNAL_RECEIPT_LOGGED"}, VERIFY: {"EXTERNAL_RECEIPT_LOGGED"}}
        if state not in states[action]: codes.append("ERR_INVALID_TICKET_STATE_TRANSITION")
        phase = next(p for p in c["phases"] if p["phase_seq"] == target["phase_seq"])
        if action in {AUTHORIZE, HANDOFF}:
            if c["commitment_status"] not in {"ACTIVE", "PARTIALLY_FULFILLED"}: codes.append("ERR_HANDOFF_DEPENDENCY_INVALIDATED")
            if any(idx["tickets"][p["ticket_id"]]["cumulative_verified_minor"] != p["allocated_minor"] for p in c["phases"] if p["phase_seq"] < target["phase_seq"]): codes.append("ERR_PREDECESSOR_PHASE_INCOMPLETE")
            if (action == AUTHORIZE or phase["require_fresh_compliance_at_handoff"]) and not metric_compliant(ctx, c, phase, case): codes.append("ERR_METRIC_COMPLIANCE_UNMET")
        if action == AUTHORIZE:
            used = {p["authorization_id"] for t in idx["tickets"].values() for p in t["handoff_authorization_history"] + ([t["handoff_authorization"]] if t["handoff_authorization"] else [])}
            if payload["authorization_id"] in used: codes.append("ERR_NEW_OBJECT_ID_CONFLICT")
            if not now < payload["handoff_expires_at"] <= scope["effective_until"]: codes.append("ERR_HANDOFF_AUTHORIZATION_EXPIRED")
        elif action == HANDOFF:
            auth = target["handoff_authorization"]
            if auth is None: codes.append("ERR_HANDOFF_DEPENDENCY_INVALIDATED")
            else:
                if now >= auth["handoff_expires_at"]: codes.append("ERR_HANDOFF_AUTHORIZATION_EXPIRED")
                if (auth["status"] != "ACTIVE" or auth["authorized_ticket_version_seq"] != target["version_seq"] or auth["commitment_terms_version_seq"] != c["terms_version_seq"] or auth["case_rule_version_seq"] != case["rule_version_seq"] or auth["template_policy_sha256"] != digest(case["template_sod_policy"])):
                    codes.append("ERR_HANDOFF_DEPENDENCY_INVALIDATED")
                origin = next((m for m in ctx["authority_snapshots"] if m["mandate_id"] == auth["origin_mandate_id"]), None)
                if ctx["origin_authority_source_status"] == "UNAVAILABLE": codes.append("ERR_MANDATE_SOURCE_UNAVAILABLE")
                elif (origin is None or origin["version_hash"] != auth["origin_mandate_version_hash"] or origin["principal_id"] != auth["authorizer_principal_id"] or origin["status"] != "ACTIVE" or not origin["valid_from"] <= now < origin["valid_until"] or AUTHORIZE not in origin["powers"] or (origin["tenant_node_id"], origin["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]) or pool_id not in origin["allowed_pool_ids"] or origin["resource_spec"] != pool["resource_spec"] or target["allocated_minor"] > origin["max_amount_minor"] or not set(a.ancestors(target["object_id"], resources, orders)) & set(origin["allowed_target_ids"])):
                    codes.append("ERR_HANDOFF_DEPENDENCY_INVALIDATED")
        elif action == LOG:
            entries = [(t, r) for t in idx["tickets"].values() for r in t["receipt_entries"]]
            if any(r["receipt_id"] == payload["receipt_id"] or (t["parent_object_id"] == c["object_id"] and r["external_reference_number"] == payload["external_reference_number"]) for t, r in entries): codes.append("ERR_DUPLICATE_RECEIPT")
            if payload["claimed_amount_minor"] > target["allocated_minor"]: codes.append("ERR_VERIFIED_AMOUNT_EXCEEDED")
        elif action == VERIFY:
            receipt_entry = next((r for r in target["receipt_entries"] if r["receipt_id"] == payload["target_receipt_id"]), None)
            if receipt_entry is None or receipt_entry["logged_at_ticket_version_seq"] != payload["target_receipt_logged_version_seq"]: codes.append("ERR_RECEIPT_REFERENCE_MISMATCH")
            if any(v["ticket_id"] == target["object_id"] and v["target_receipt_id"] == payload["target_receipt_id"] and v["verification_outcome"] == "VERIFIED_FULFILLED" for v in idx["execution_verifications"].values()): codes.append("ERR_RECEIPT_ALREADY_VERIFIED")
            delta = payload["verified_delta_minor"]
            if payload["verification_outcome"] == "VERIFIED_FULFILLED":
                if delta <= 0 or (receipt_entry is not None and delta > receipt_entry["claimed_amount_minor"]) or target["cumulative_verified_minor"] + delta > target["allocated_minor"] or c["verified_fulfilled_minor"] + delta > c["total_obligated_minor"]: codes.append("ERR_VERIFIED_AMOUNT_EXCEEDED")
            elif delta != 0: codes.append("ERR_VERIFIED_AMOUNT_EXCEEDED")
            if payload["verification_id"] in {ident for group in idx.values() for ident in group} | {ctx["new_envelope_id"]}: codes.append("ERR_NEW_OBJECT_ID_CONFLICT")
    # Keep shared 2A priorities independent of which dependency discovered an outage.
    priority = {"ERR_BLOCKED_BY_PROTECTIVE_ORDER": 0, "ERR_MANDATE_SOURCE_UNAVAILABLE": 1, "ERR_MANDATE_LAPSED_OR_REVOKED": 2, "ERR_UNAUTHORIZED_POWER": 2, "ERR_MANDATE_SCOPE_MISMATCH": 2, "ERR_UNKNOWN_MANDATE": 2, "ERR_EFFECTIVENESS_SCOPE_MISMATCH": 3, "ERR_RESOURCE_POOL_EXHAUSTED": 3, "ERR_RESOURCE_SCOPE_MISMATCH": 3, "ERR_SOD_IDENTITY_UNMAPPED": 4, "ERR_SOD_CONFLICT": 4, "ERR_SOD_TEMPLATE_EXECUTOR_CONFLICT": 4}
    return sorted(dict.fromkeys(codes), key=lambda code: priority.get(code, 5)), matched


def expected_applied_domain(data):
    domain = copy.deepcopy(data["before"]["domain_objects"])
    idx = domain_index(domain)
    cmd, ctx = data["command"], data["server_context"]
    action, payload, env_id = cmd["requested_action"], cmd["payload"], ctx["new_envelope_id"]
    case = idx["cases"][ctx["case_id"]]
    versions = []
    def changed(row):
        row["version_seq"] += 1
        versions.append({"object_id": row["object_id"], "version_seq": row["version_seq"]})
    if action == APPROVE:
        new = payload["new_commitment"]; pool = idx["resource_pools"][new["budget_pool_id"]]
        pool["committed_obligations_minor"] += new["total_obligated_minor"]; changed(pool)
        c = {"object_id": new["commitment_id"], "kind": "COMMITMENT", "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"], "parent_object_id": ctx["case_id"], "version_seq": 1, "budget_pool_id": pool["object_id"], "resource_spec": copy.deepcopy(new["resource_spec"]), "total_obligated_minor": new["total_obligated_minor"], "verified_fulfilled_minor": 0, "terms_version_seq": 1, "commitment_status": "ACTIVE", "executor_member_ids": copy.deepcopy(new["executor_member_ids"]), "phases": copy.deepcopy(new["phases"]), "creation_envelope_id": env_id}
        domain["commitments"].append(c); versions.append({"object_id": c["object_id"], "version_seq": 1})
        members = {m["ticket_id"]: m["member_id"] for m in new["ticket_executor_member_ids"]}
        for phase in new["phases"]:
            ticket = {"object_id": phase["ticket_id"], "kind": "TICKET", "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"], "parent_object_id": c["object_id"], "version_seq": 1, "phase_seq": phase["phase_seq"], "resource_spec": copy.deepcopy(c["resource_spec"]), "allocated_minor": phase["allocated_minor"], "cumulative_verified_minor": 0, "ticket_status": "QUEUED_INTERNAL", "assignee_executor_member_id": members[phase["ticket_id"]], "creation_envelope_id": env_id, "handoff_authorization": None, "handoff_authorization_history": [], "handoff_record": None, "receipt_entries": [], "verification_ids": []}
            domain["tickets"].append(ticket); versions.append({"object_id": ticket["object_id"], "version_seq": 1})
    else:
        ticket = idx["tickets"][cmd["target_object_id"]]; c = idx["commitments"][ticket["parent_object_id"]]
        changed(ticket)
        if action == CANCEL:
            ticket["ticket_status"] = "CANCELLED"
        elif action == AUTHORIZE:
            if ticket["handoff_authorization"] is not None:
                ticket["handoff_authorization_history"].append(copy.deepcopy(ticket["handoff_authorization"]))
            ticket["ticket_status"] = "READY_FOR_HANDOFF"
            ticket["handoff_authorization"] = {"authorization_id": payload["authorization_id"], "status": "ACTIVE", "authorizer_principal_id": ctx["authenticated_principal_id"], "authorizer_member_id": ctx["authenticated_member_id"], "origin_mandate_id": cmd["mandate_claim"]["mandate_id"], "origin_mandate_version_hash": cmd["mandate_claim"]["expected_version_hash"], "authorized_ticket_version_seq": ticket["version_seq"], "handoff_expires_at": payload["handoff_expires_at"], "commitment_terms_version_seq": c["terms_version_seq"], "case_rule_version_seq": case["rule_version_seq"], "template_policy_sha256": digest(case["template_sod_policy"]), "envelope_id": env_id}
        elif action == HANDOFF:
            ticket["ticket_status"] = "HANDED_OFF_TO_EXTERNAL"
            ticket["handoff_record"] = {"operator_member_id": ctx["authenticated_member_id"], "operator_principal_id": ctx["authenticated_principal_id"], "recipient_reference": payload["recipient_reference"], "recorded_at": ctx["evaluated_at"], "authorization_id": ticket["handoff_authorization"]["authorization_id"], "envelope_id": env_id}
        elif action == LOG:
            ticket["ticket_status"] = "EXTERNAL_RECEIPT_LOGGED"
            ticket["receipt_entries"].append({"receipt_id": payload["receipt_id"], "logged_at_ticket_version_seq": ticket["version_seq"], "external_reference_number": payload["external_reference_number"], "attachment_sha256": payload["attachment_sha256"], "claimed_amount_minor": payload["claimed_amount_minor"], "envelope_id": env_id})
        else:
            receipt = next(r for r in ticket["receipt_entries"] if r["receipt_id"] == payload["target_receipt_id"])
            v = {"verification_id": payload["verification_id"], "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"], "version_seq": 1, "ticket_id": ticket["object_id"], "ticket_evaluated_version_seq": cmd["target_expected_version_seq"], "target_receipt_id": receipt["receipt_id"], "target_receipt_logged_version_seq": receipt["logged_at_ticket_version_seq"], "receipt_attachment_sha256": receipt["attachment_sha256"], "verification_outcome": payload["verification_outcome"], "verified_delta_minor": payload["verified_delta_minor"], "verifier_member_id": ctx["authenticated_member_id"], "envelope_id": env_id}
            domain["execution_verifications"].append(v); ticket["verification_ids"].append(v["verification_id"])
            versions.append({"object_id": v["verification_id"], "version_seq": 1})
            if v["verification_outcome"] == "VERIFIED_FULFILLED":
                ticket["cumulative_verified_minor"] += v["verified_delta_minor"]
                c["verified_fulfilled_minor"] += v["verified_delta_minor"]; changed(c)
                c["commitment_status"] = "COMPLETED" if c["verified_fulfilled_minor"] == c["total_obligated_minor"] else "PARTIALLY_FULFILLED"
    versions.sort(key=lambda row: row["object_id"])
    inp = payload["effectiveness_envelope_input"]
    domain["effectiveness_envelopes"].append({"envelope_id": env_id, "action_type": action, "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "resulting_versions": versions, "mandate_ref": {"mandate_id": cmd["mandate_claim"]["mandate_id"], "version_hash": cmd["mandate_claim"]["expected_version_hash"], "clause_reference": inp["mandate_clause_ref"]}, "auth_check_receipt": copy.deepcopy(ctx["generated_auth_receipt"]), "evidence_refs": copy.deepcopy(inp["evidence_refs"]), "scope_spec": copy.deepcopy(inp["scope_spec"]), "remedy_spec": copy.deepcopy(inp["remedy_spec"]), "effect_parameters_sha256": digest(payload)})
    return domain, versions


def validate_semantics(data):
    idx, target = validate_basis(data)
    before, ctx, cmd, after = data["before"], data["server_context"], data["command"], data["after"]
    out = after["execution_outcome"]
    observation = ctx["execution_observation"]
    if observation != "NORMAL":
        unknown = observation == "COMMIT_RESPONSE_LOST"
        require(ctx["generated_auth_receipt"] is None, "ERR_FAULT_CLAIMS_COMPLETED_AUTH", "/server_context/generated_auth_receipt")
        require(out["outcome"] == ("OUTCOME_UNKNOWN" if unknown else "SYSTEM_FAULT"), "ERR_FAULT_OUTCOME_MISMATCH", "/after/execution_outcome/outcome")
        require(after["domain_objects"] == (None if unknown else before["domain_objects"]), "ERR_FAULT_CLAIMS_DOMAIN_STATE", "/after/domain_objects")
        require(out["command_record"] == (None if unknown else before["command_record"]) and out["command_attempt"] == (None if unknown else before["command_attempt"]), "ERR_FAULT_REWRITES_COMMAND_STATE", "/after/execution_outcome")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "observed_at": ctx["evaluated_at"], "kind": observation, "recovery_action": "RECONCILE_REQUIRED" if unknown else "RETRY_AFTER_RECONCILIATION", "persistence_claim": "NOT_ASSERTED"}
        require(out["fault_observation"] == expected, "ERR_FAULT_OBSERVATION_MISMATCH", "/after/execution_outcome/fault_observation")
        return
    codes, matched = decision_diagnostics(data, idx, target)
    expected_status = "REJECTED" if codes else "APPLIED"
    require(out["outcome"] == expected_status, "ERR_DECLARED_OUTCOME_MISMATCH", "/after/execution_outcome/outcome")
    if codes:
        domain, versions = before["domain_objects"], []
        require(after["domain_objects"] == domain, "ERR_REJECTED_DOMAIN_MUTATION", "/after/domain_objects")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "rejection_error_code": codes[0], "diagnostic_codes": codes, "blocking_protective_order_ids": matched}
        require(out["command_rejection"] == expected, "ERR_REJECTION_DIAGNOSTIC_MISMATCH", "/after/execution_outcome/command_rejection")
    else:
        domain, versions = expected_applied_domain(data)
        require(after["domain_objects"] == domain, "ERR_APPLIED_WRITE_SET_MISMATCH", "/after/domain_objects")
        validate_domain(domain)
    require(out["auth_check_receipt"] == ctx["generated_auth_receipt"], "ERR_RECEIPT_BINDING_MISMATCH", "/after/execution_outcome/auth_check_receipt")
    retryable = codes == ["ERR_MANDATE_SOURCE_UNAVAILABLE"]
    result = out["command_result"]
    expected = {"result_id": result["result_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "status": expected_status, "retry_disposition": "NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY" if retryable else "FINAL", "applied_versions": versions}
    require(result == expected, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
    record = copy.deepcopy(before["command_record"]); attempt = copy.deepcopy(before["command_attempt"])
    record.update(version_seq=record["version_seq"] + 1, status="IN_FLIGHT" if retryable else expected_status, result_id=result["result_id"])
    attempt.update(version_seq=attempt["version_seq"] + 1, status="FAILED_RETRYABLE" if retryable else expected_status)
    require(out["command_record"] == record and out["command_attempt"] == attempt, "ERR_COMMAND_RESULT_BINDING_MISMATCH", "/after/execution_outcome")
    audit = out["audit_event"]
    expected = {"audit_id": audit["audit_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "outcome": expected_status, "checked_at": ctx["evaluated_at"], "before_domain_sha256": digest(before["domain_objects"]), "after_domain_sha256": digest(domain), "written_versions": versions}
    require(audit == expected, "ERR_AUDIT_BINDING_MISMATCH", "/after/execution_outcome/audit_event")


def validate_transition(data):
    registry = check_registry()
    if not registry["is_valid"]: return registry
    for validator in [COMMON_VALIDATOR, VALIDATOR]:
        error = a.schema_error(validator, data)
        if error: return error
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
    args = parser.parse_args(); failed = False
    for path in args.fixtures:
        result = validate_file(path); failed |= not result["is_valid"]
        print(json.dumps({"input": str(path), **result}, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
