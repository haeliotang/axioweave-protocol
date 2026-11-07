"""Slice 2C synthetic offline appeal and collective-authorization contracts."""

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
SPEC = ROOT / "specs/aw001_slice2/2c"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)
SUBMIT, OPEN, RULE, ADJUDICATE, DEFER, APPROVE = (
    "SUBMIT_APPEAL", "OPEN_APPEAL_ADMISSIBILITY_REVIEW", "RULE_APPEAL_ADMISSIBILITY",
    "ADJUDICATE_APPEAL", "RECORD_SOD_DEFERRAL", "APPROVE_COMMITMENT_COLLECTIVE",
)
POWERS = dict(zip((SUBMIT, OPEN, RULE, ADJUDICATE, DEFER, APPROVE),
                  (None, "REVIEW_APPEAL_ADMISSIBILITY", "REVIEW_APPEAL_ADMISSIBILITY", ADJUDICATE, "ESCALATE_OR_DEFER_FOR_SOD", b.APPROVE)))
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION", "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_INDIVIDUAL_AUTHORITY_AND_CONSENT", "CHECK_QUALIFIED_POOL", "CHECK_HISTORICAL_RECUSAL", "CHECK_ALLOWED_WRITE_SET", "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = dict(zip(POWERS, ("CREATE_APPEAL_ONLY", "OPEN_REVIEW_ONLY", "ADMISSIBILITY_ONLY", "MERITS_ONLY", "DEFERRAL_ONLY", "UPDATE_POOL_CREATE_COMMITMENT_ALL_TICKETS")))
LOCKS = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder", "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE", "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED", "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED", "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER", "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH", "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}


def check_registry(path=SPEC / "fixed-action-registry.json"):
    try:
        r = load_json(path)
        require(isinstance(r, dict) and set(r) == {"contract_version", "global_lock_order", "actions"} and r["contract_version"] == "0.3.0-rc1-slice2c" and isinstance(r["actions"], dict) and set(r["actions"]) == set(POWERS), "ERR_ACTION_REGISTRY_VIOLATION", "/")
        require(r["global_lock_order"] == LOCKS, "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/global_lock_order")
        for action, power in POWERS.items():
            e = r["actions"][action]
            require(isinstance(e, dict) and isinstance(e.get("required_validators"), list) and all(v in e["required_validators"] for v in CHECKS), "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING", "/actions/" + action)
            require(e.get("lock_levels") == [0, 1, 2, 3, 4] and all(type(v) is int for v in e["lock_levels"]), "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/actions/" + action)
            require(e == {"required_power": power, "requires_effectiveness_envelope": power is not None, "required_validators": CHECKS, "lock_levels": [0, 1, 2, 3, 4], "write_policy": WRITES[action]}, "ERR_ACTION_REGISTRY_VIOLATION", "/actions/" + action)
    except (OSError, ValueError):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    except ContractFailure as error:
        return {**error.report, "validation_layer": "REGISTRY_LOADER"}
    return report("REGISTRY_LOADER")


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


def object_index(domain):
    ops = domain["operations"]
    result = {r["object_id"]: r for name in ("cases", "resource_pools", "commitments", "tickets") for r in ops[name]}
    result.update({r["order_id"]: {**r, "object_id": r["order_id"], "kind": "PROTECTIVE_ORDER"} for r in ops["protective_orders"]})
    result.update({r["object_id"]: r for r in domain["appeals"]})
    return result


def envelope_index(domain):
    return {e["envelope_id"]: e for e in domain["operations"]["effectiveness_envelopes"] + domain["operations"]["protection_envelopes"] + domain["procedure_envelopes"]}


def operational_envelope(env):
    """An explicit compatibility view; keep the full collective envelope alongside it."""
    result = {k: copy.deepcopy(env[k]) for k in b.SCHEMA["$defs"]["Envelope"]["required"]}
    result.update(envelope_id=env["projected_operational_envelope_id"], action_type=b.APPROVE,
                  effect_parameters_sha256=digest({k: v for k, v in env["payload_snapshot"].items() if k != "panel_principal_ids"}))
    return result


def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    objects, envs = object_index(domain), envelope_index(domain)
    ids = [r["object_id"] for name in ("cases", "resource_pools", "commitments", "tickets") for r in domain["operations"][name]]
    ids += [r["order_id"] for r in domain["operations"]["protective_orders"]] + [r["object_id"] for r in domain["appeals"]]
    all_envs = domain["operations"]["effectiveness_envelopes"] + domain["operations"]["protection_envelopes"] + domain["procedure_envelopes"]
    require(len(ids) == len(set(ids)) and len(envs) == len(all_envs) and not set(ids) & set(envs), "ERR_DUPLICATE_OBJECT_ID", "/domain_objects")
    policies = domain["case_policies"]
    require(len({p["case_id"] for p in policies}) == len(policies), "ERR_POLICY_BINDING_MISMATCH", "/domain_objects/case_policies")
    for p in policies:
        c = objects.get(p["case_id"])
        require(c is not None and c["kind"] == "CASE" and p["tenant_node_id"] == c["tenant_node_id"] and p["case_rule_version_seq"] == c["rule_version_seq"], "ERR_POLICY_BINDING_MISMATCH", "/domain_objects/case_policies")
    bindings = domain["historical_actor_bindings"]
    require(len({(r["envelope_id"], r["principal_id"]) for r in bindings}) == len(bindings), "ERR_HISTORICAL_IDENTITY_BINDING", "/domain_objects/historical_actor_bindings")
    for r in bindings:
        e = envs.get(r["envelope_id"])
        require(e is not None and r["envelope_sha256"] == digest(e) and e["auth_check_receipt"]["authenticated_principal_id"] == r["principal_id"], "ERR_HISTORICAL_IDENTITY_BINDING", "/domain_objects/historical_actor_bindings")
    for e in domain["procedure_envelopes"]:
        receipt = e["auth_check_receipt"]; action = e["action_type"]
        require(action != SUBMIT and receipt["verification_status"] == "VERIFIED_ACTIVE" and receipt["requested_power"] == POWERS[action]
                and receipt["target_object_id"] == e["target_object_id"] and receipt["evaluated_target_version_seq"] == e["evaluated_target_version_seq"]
                and receipt["mandate_id"] == e["mandate_ref"]["mandate_id"] and receipt["evaluated_version_hash"] == e["mandate_ref"]["version_hash"]
                and e["effect_parameters_sha256"] == digest(e["payload_snapshot"]), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
        require(all(v["object_id"] in objects and v["version_seq"] <= objects[v["object_id"]]["version_seq"] for v in e["resulting_versions"]), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
        members = e["collective_member_attestations"]
        require(len({m["member_id"] for m in members}) == len(members), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
        if action != DEFER:
            require(members and e["actor_member_id"] in {m["member_id"] for m in members}, "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
            require(len({m["principal_id"] for m in members}) == len(members)
                    and set(e["payload_snapshot"]["panel_principal_ids"]) == {m["principal_id"] for m in members}
                    and any(m["principal_id"] == receipt["authenticated_principal_id"] and m["member_id"] == e["actor_member_id"] for m in members), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
            for m in members:
                consent = m["consent_binding"]; mr = m["member_auth_receipt"]
                authority = m["authority_snapshot"]; policy = e["policy_snapshot"]
                require(consent["status"] == "CONFIRMED" and consent["consented_payload_sha256"] == e["effect_parameters_sha256"]
                        and consent["evaluated_target_version_seq"] == e["evaluated_target_version_seq"] and mr["verification_status"] == "VERIFIED_ACTIVE"
                        and mr["principal_id"] == m["principal_id"] and mr["member_id"] == m["member_id"] and mr["requested_power"] == POWERS[action], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
                require(mr["target_object_id"] == e["target_object_id"] and mr["evaluated_target_version_seq"] == e["evaluated_target_version_seq"]
                        and mr["checked_at"] == receipt["checked_at"] and mr["mandate_id"] == authority["mandate_id"] and mr["evaluated_version_hash"] == authority["version_hash"]
                        and authority["principal_id"] == m["principal_id"] and authority["status"] == "ACTIVE" and authority["valid_from"] <= mr["checked_at"] < authority["valid_until"]
                        and (mr["tenant_node_id"], mr["case_id"]) == (authority["tenant_node_id"], authority["case_id"]) == (policy["tenant_node_id"], policy["case_id"])
                        and POWERS[action] in authority["powers"] and {e["target_object_id"], policy["case_id"]} & set(authority["allowed_target_ids"])
                        and consent["policy_version_seq"] == policy["version_seq"] and consent["case_rule_version_seq"] == policy["case_rule_version_seq"]
                        and consent["commitment_terms_version_seq"] == (1 if action == APPROVE else None), "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
                if action == APPROVE:
                    new = e["payload_snapshot"]["new_commitment"]
                    require(new["total_obligated_minor"] <= authority["max_amount_minor"] and new["resource_spec"] == authority["resource_spec"] and new["budget_pool_id"] in authority["allowed_pool_ids"], "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
        if action == APPROVE:
            require(envs.get(e["projected_operational_envelope_id"]) == operational_envelope(e), "ERR_COLLECTIVE_PROJECTION_MISMATCH", "/domain_objects/procedure_envelopes")
        else:
            require(e["projected_operational_envelope_id"] is None, "ERR_INVALID_EFFECT_HISTORY", "/domain_objects/procedure_envelopes")
    for appeal in domain["appeals"]:
        case = objects.get(appeal["case_id"])
        require(case is not None and case["kind"] == "CASE" and appeal["parent_object_id"] == case["object_id"] and appeal["tenant_node_id"] == case["tenant_node_id"], "ERR_INVALID_OBJECT_HIERARCHY", "/domain_objects/appeals")
        validate_challenge(domain, appeal)
        adm, merits, stage = appeal["admissibility_record"], appeal["merits_adjudication_record"], appeal["appeal_status"]
        require((stage in {"SUBMITTED", "ADMISSIBILITY_REVIEW"} and adm is None and merits is None)
                or (stage == "ADJUDICATING" and adm is not None and adm["outcome"] == "ACCEPTED" and merits is None)
                or (stage == "RESOLVED" and adm is not None and ((adm["outcome"] == "REJECTED_INADMISSIBLE" and merits is None) or (adm["outcome"] == "ACCEPTED" and merits is not None))), "ERR_INVALID_APPEAL_HISTORY", "/domain_objects/appeals")
        references = [(appeal["opening_envelope_id"], OPEN)] if appeal["opening_envelope_id"] else []
        references += [(r["decision_envelope_id"], act) for r, act in ((adm, RULE), (merits, ADJUDICATE), (appeal["sod_deferral_record"], DEFER)) if r]
        require((stage == "SUBMITTED") == (appeal["opening_envelope_id"] is None), "ERR_INVALID_APPEAL_HISTORY", "/domain_objects/appeals")
        for ident, act in references:
            e = envs.get(ident)
            require(e is not None and e["action_type"] == act and e["target_object_id"] == appeal["object_id"], "ERR_INVALID_APPEAL_HISTORY", "/domain_objects/appeals")
        if adm:
            e = envs[adm["decision_envelope_id"]]; p = e["payload_snapshot"]
            require(adm["outcome"] == p["outcome"] and adm["rationale"] == p["rationale"]
                    and adm["panel_member_ids"] == sorted(m["member_id"] for m in e["collective_member_attestations"]), "ERR_INVALID_APPEAL_HISTORY", "/domain_objects/appeals/admissibility_record")
        if merits:
            e = envs[merits["decision_envelope_id"]]; p = e["payload_snapshot"]
            require(all(merits[k] == p[k] for k in ("outcome", "rationale", "required_remedy_directives"))
                    and merits["panel_member_ids"] == sorted(m["member_id"] for m in e["collective_member_attestations"])
                    and (merits["outcome"] == "UPHELD_REMEDY_REQUIRED") == bool(merits["required_remedy_directives"]), "ERR_INVALID_APPEAL_HISTORY", "/domain_objects/appeals/merits_adjudication_record")
    return objects


def project_operations(domain):
    validate_domain(domain)
    return copy.deepcopy(domain["operations"])


def validate_challenge(domain, appeal):
    objects, envs = object_index(domain), envelope_index(domain)
    target, env = objects.get(appeal["challenged_object_id"]), envs.get(appeal["challenged_decision_envelope_id"])
    version = appeal["challenged_object_version_seq"]
    require(target is not None and target["kind"] == appeal["challenged_object_kind"] and version <= target["version_seq"] and (target["tenant_node_id"], target["case_id"]) == (appeal["tenant_node_id"], appeal["case_id"])
            and env is not None and {"object_id": target["object_id"], "version_seq": version} in env["resulting_versions"], "ERR_CHALLENGED_DECISION_REFERENCE", "/appeal/challenged_decision_envelope_id")


def recusal(domain, appeal, policy):
    """Trace only direct responsibility and explicit policy extensions, never the whole case DAG."""
    objects, envs, trace = object_index(domain), envelope_index(domain), []
    projections = {e["projected_operational_envelope_id"]: e for e in domain["procedure_envelopes"] if e["projected_operational_envelope_id"]}
    def add(member, reason, source):
        trace.append({"member_id": member if b.known_member(member) else None, "reason": reason, "source_id": source})
    def signers(ident, reason):
        e = projections.get(ident, envs[ident])
        if "actor_member_id" in e:
            for member in ([m["member_id"] for m in e["collective_member_attestations"]] or [e["actor_member_id"]]): add(member, reason, ident)
        else:
            principal = e["auth_check_receipt"]["authenticated_principal_id"]
            mapping = next((r for r in domain["historical_actor_bindings"] if r["envelope_id"] == ident and r["principal_id"] == principal), None)
            add(mapping["member_id"] if mapping and mapping["status"] == "VERIFIED" else None, reason, ident)
    for m in appeal["appellant_member_ids"]: add(m, "APPELLANT", appeal["object_id"])
    signers(appeal["challenged_decision_envelope_id"], "CHALLENGED_SIGNER")
    target = objects[appeal["challenged_object_id"]]; c = None
    if target["kind"] == "TICKET":
        c = objects[target["parent_object_id"]]
        add(target["assignee_executor_member_id"], "CURRENT_EXECUTOR", target["object_id"])
        for p in target["handoff_authorization_history"] + ([target["handoff_authorization"]] if target["handoff_authorization"] else []):
            add(p["authorizer_member_id"], "DIRECT_AUTHORIZER", p["envelope_id"]); signers(p["envelope_id"], "DIRECT_AUTHORIZER_SIGNER")
        if target["handoff_record"]:
            add(target["handoff_record"]["operator_member_id"], "DIRECT_OPERATOR", target["handoff_record"]["envelope_id"])
        for v in domain["operations"]["execution_verifications"]:
            if v["ticket_id"] == target["object_id"]:
                add(v["verifier_member_id"], "CURRENT_VERIFIER", v["verification_id"])
        for e in domain["operations"]["effectiveness_envelopes"]:
            if e["target_object_id"] == target["object_id"] and e["action_type"] in {b.AUTHORIZE, b.HANDOFF, b.VERIFY} and any(v["object_id"] == target["object_id"] and v["version_seq"] <= appeal["challenged_object_version_seq"] for v in e["resulting_versions"]): signers(e["envelope_id"], "HISTORICAL_DIRECT_SIGNER")
        if policy["include_parent_commitment_approvers_on_ticket_appeal"]: signers(c["creation_envelope_id"], "POLICY_PARENT_APPROVER")
    elif target["kind"] == "COMMITMENT":
        c = target
        signers(c["creation_envelope_id"], "DIRECT_APPROVER")
    else:
        signers(target["issuance_envelope_id"], "ORIGINAL_ORDER_ISSUER")
        resources, orders, _ = b.a.domain_index(b.protection_projection(domain["operations"]))
        for t in domain["operations"]["tickets"]:
            if b.a.covers(target, t["object_id"], resources, orders): add(t["assignee_executor_member_id"], "RESTRICTED_EXECUTOR", t["object_id"])
    if policy["include_case_proposer_in_all_appeals"]: add(objects[appeal["case_id"]]["proposer_member_id"], "POLICY_PROPOSER", appeal["case_id"])
    if c is not None and policy["include_commitment_executors_on_appeal"]:
        for member in c["executor_member_ids"]: add(member, "POLICY_COMMITMENT_EXECUTOR", c["object_id"])
    unique = {(r["member_id"], r["reason"], r["source_id"]): r for r in trace}
    trace = sorted(unique.values(), key=lambda r: (r["source_id"], r["reason"], r["member_id"] or ""))
    return {r["member_id"] for r in trace if r["member_id"]}, trace


def member(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" and b.known_member(row["member_id"]) else None


def authority_status(data, principal, mandate_id, power, expected_hash=None):
    cmd, ctx = data["command"], data["server_context"]
    if ctx["authority_source_status"] != "AVAILABLE": return "SOURCE_UNAVAILABLE"
    m = next((m for m in ctx["authority_snapshots"] if m["mandate_id"] == mandate_id), None)
    if m is None: return "UNKNOWN_MANDATE"
    if m["status"] == "REVOKED": return "REVOKED"
    if not m["valid_from"] <= ctx["evaluated_at"] < m["valid_until"]: return "EXPIRED"
    if power not in m["powers"]: return "POWER_NOT_GRANTED"
    if m["principal_id"] != principal or (expected_hash is not None and m["version_hash"] != expected_hash) or (m["tenant_node_id"], m["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"]) or not {cmd["target_object_id"], ctx["case_id"]} & set(m["allowed_target_ids"]): return "SCOPE_MISMATCH"
    if cmd["requested_action"] == APPROVE:
        n = cmd["payload"]["new_commitment"]
        if n["resource_spec"] != m["resource_spec"] or n["total_obligated_minor"] > m["max_amount_minor"] or n["budget_pool_id"] not in m["allowed_pool_ids"]: return "SCOPE_MISMATCH"
    return "VERIFIED_ACTIVE"


def current_receipt(data):
    cmd, ctx = data["command"], data["server_context"]
    if cmd["requested_action"] == SUBMIT:
        require(cmd["mandate_claim"] is None and ctx["generated_auth_receipt"] is None and ctx["new_envelope_id"] is None and not ctx["collective_member_attestations"], "ERR_INTAKE_SELF_AUTHORIZATION", "/server_context")
        return None
    claim = cmd["mandate_claim"]
    require(claim is not None and ctx["generated_auth_receipt"] is not None and ctx["new_envelope_id"] is not None, "ERR_INCOMPLETE_FOUR_TUPLE", "/server_context")
    receipt = ctx["generated_auth_receipt"]
    status = authority_status(data, ctx["authenticated_principal_id"], claim["mandate_id"], POWERS[cmd["requested_action"]], claim["expected_version_hash"])
    expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "idempotency_key": cmd["idempotency_key"], "authenticated_principal_id": ctx["authenticated_principal_id"], "mandate_id": claim["mandate_id"], "evaluated_version_hash": claim["expected_version_hash"], "requested_power": POWERS[cmd["requested_action"]], "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "checked_at": ctx["evaluated_at"], "authority_source_id": ctx["authority_source_id"], "domain_sha256": digest(data["before"]["domain_objects"]), "context_sha256": context_digest(ctx), "verification_status": status}
    require(all(receipt[k] == v for k, v in expected.items()), "ERR_RECEIPT_BINDING_MISMATCH", "/server_context/generated_auth_receipt")
    return status


def qualified_pool(data, policy, power, excluded):
    ctx = data["server_context"]; pool = ctx["reviewer_pool_snapshot"]
    require(pool is not None and pool["pool_completeness_status"] == "COMPLETE" and pool["pool_valid_from"] <= ctx["evaluated_at"] < pool["pool_valid_until"]
            and (pool["tenant_node_id"], pool["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"])
            and pool["case_rule_version_seq"] == policy["case_rule_version_seq"] and pool["policy_version_seq"] == policy["version_seq"]
            and ctx["authority_inventory_complete"] and ctx["authority_source_status"] == "AVAILABLE", "ERR_REVIEWER_POOL_INDETERMINATE", "/server_context/reviewer_pool_snapshot")
    require(pool["source_ref"] in ctx["evidence_snapshots"], "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/reviewer_pool_snapshot/source_ref")
    require(len({r["principal_id"] for r in pool["candidates"]}) == len(pool["candidates"]), "ERR_REVIEWER_POOL_INDETERMINATE", "/server_context/reviewer_pool_snapshot/candidates")
    return {member(ctx, r["principal_id"]) for r in pool["candidates"] if member(ctx, r["principal_id"]) and r["availability_status"] == "AVAILABLE" and member(ctx, r["principal_id"]) not in excluded and authority_status(data, r["principal_id"], r["mandate_id"], power) == "VERIFIED_ACTIVE"}


def validate_basis(data):
    cmd, ctx, before = data["command"], data["server_context"], data["before"]
    objects = validate_domain(before["domain_objects"]); target = objects.get(cmd["target_object_id"])
    require(ctx["access_granted"] and target is not None and (target["tenant_node_id"], target["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"]), "ERR_CONTEXT_SCOPE_MISMATCH", "/command/target_object_id")
    require(target["kind"] == ("CASE" if cmd["requested_action"] in {SUBMIT, APPROVE} else "APPEAL"), "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target["version_seq"], "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    policy = next((p for p in before["domain_objects"]["case_policies"] if p["case_id"] == ctx["case_id"]), None)
    require(policy is not None and policy["valid_from"] <= ctx["evaluated_at"] < policy["valid_until"], "ERR_POLICY_BINDING_MISMATCH", "/domain_objects/case_policies")
    deps = [cmd["payload"]["new_commitment"]["budget_pool_id"]] if cmd["requested_action"] == APPROVE else ([ctx["case_id"]] if target["kind"] == "APPEAL" else [])
    require(len(cmd["dependency_versions"]) == len(deps) and {v["object_id"] for v in cmd["dependency_versions"]} == set(deps) and all(v["object_id"] in objects and objects[v["object_id"]]["version_seq"] == v["version_seq"] for v in cmd["dependency_versions"]), "ERR_DEPENDENCY_VERSION_MISMATCH", "/command/dependency_versions")
    record, attempt = before["command_record"], before["command_attempt"]
    expected = {"command_id": cmd["command_id"], "tenant_node_id": ctx["tenant_node_id"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "idempotency_key": cmd["idempotency_key"], "request_sha256": digest(cmd), "current_attempt_id": ctx["attempt_id"], "generation": ctx["generation"]}
    require(record["status"] == attempt["status"] == "IN_FLIGHT" and record["result_id"] is None and all(record[k] == v for k, v in expected.items()), "ERR_COMMAND_BINDING_MISMATCH", "/before/command_record")
    require(attempt["attempt_id"] == ctx["attempt_id"] and attempt["command_id"] == cmd["command_id"] and attempt["generation"] == ctx["generation"], "ERR_ATTEMPT_GENERATION_MISMATCH", "/before/command_attempt")
    for collection, key in (("member_directory", "principal_id"), ("authority_snapshots", "mandate_id")):
        require(len({r[key] for r in ctx[collection]}) == len(ctx[collection]), "ERR_CONTEXT_SNAPSHOT_CONFLICT", "/server_context/" + collection)
    refs = [policy["source_ref"]] + [m["source_ref"] for m in ctx["member_directory"]] + [m["source_ref"] for m in before["domain_objects"]["historical_actor_bindings"]]
    if cmd["requested_action"] != SUBMIT:
        inp = cmd["payload"]["effectiveness_envelope_input"]
        refs += inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    require(all(e in ctx["evidence_snapshots"] for e in refs), "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    return objects, target, policy


def decision_diagnostics(data, objects, target, policy, status):
    cmd, ctx = data["command"], data["server_context"]; action = cmd["requested_action"]; p = cmd["payload"]
    domain = data["before"]["domain_objects"]; trace, panel, eligible = [], [], set()
    try:
        require(member(ctx, ctx["authenticated_principal_id"]) is not None and member(ctx, ctx["authenticated_principal_id"]) == ctx["authenticated_member_id"], "ERR_SOD_IDENTITY_UNMAPPED", "/server_context/authenticated_member_id")
        if action == SUBMIT:
            check = {"principal_id": ctx["authenticated_principal_id"], "member_id": ctx["authenticated_member_id"], "case_id": ctx["case_id"], "policy_version_seq": policy["version_seq"], "checked_at": ctx["evaluated_at"], "accepted": policy["intake_open"]}
            require(ctx["intake_policy_check"] == check and policy["intake_open"], "ERR_INTAKE_POLICY_DENIED", "/server_context/intake_policy_check")
            validate_challenge(domain, {**p, "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"]})
            require(p["appeal_id"] not in objects and p["appeal_id"] not in envelope_index(domain), "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/appeal_id")
            return [], trace, panel, eligible
        require(status == "VERIFIED_ACTIVE", STATUS_CODES.get(status, "ERR_UNAUTHORIZED_POWER"), "/server_context/generated_auth_receipt")
        require(cmd["mandate_claim"]["claimed_power"] == POWERS[action], "ERR_UNAUTHORIZED_POWER", "/command/mandate_claim")
        require(ctx["new_envelope_id"] not in objects and ctx["new_envelope_id"] not in envelope_index(domain), "ERR_NEW_OBJECT_ID_CONFLICT", "/server_context/new_envelope_id")
        inp, now = p["effectiveness_envelope_input"], ctx["evaluated_at"]
        scope = inp["scope_spec"]
        current = next(m for m in ctx["authority_snapshots"] if m["mandate_id"] == cmd["mandate_claim"]["mandate_id"])
        require(inp["mandate_clause_ref"] == current["clause_reference"] and (scope["tenant_node_id"], scope["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"]) and scope["effective_from"] <= now < scope["effective_until"] <= current["valid_until"], "ERR_EFFECTIVENESS_SCOPE_MISMATCH", "/command/payload/effectiveness_envelope_input")
        if action == APPROVE:
            excluded = {objects[ctx["case_id"]]["proposer_member_id"]}; quorum = policy["quorums"]["approval"]; pool_power = b.APPROVE
            require(all(b.known_member(m) for m in excluded), "ERR_SOD_IDENTITY_UNMAPPED", "/case/proposer_member_id")
        else:
            allowed = {OPEN: {"SUBMITTED"}, RULE: {"ADMISSIBILITY_REVIEW"}, ADJUDICATE: {"ADJUDICATING"}, DEFER: {"SUBMITTED", "ADMISSIBILITY_REVIEW", "ADJUDICATING"}}
            require(target["appeal_status"] in allowed[action], "ERR_INVALID_APPEAL_STATE_TRANSITION", "/appeal/appeal_status")
            excluded, trace = recusal(domain, target, policy["sod_recusal_policy"])
            require(all(r["member_id"] for r in trace), "ERR_SOD_IDENTITY_UNMAPPED", "/recusal_trace")
            adjudicating = action == ADJUDICATE or (action == DEFER and target["appeal_status"] == "ADJUDICATING")
            quorum = policy["quorums"]["adjudication" if adjudicating else "admissibility"]
            pool_power = ADJUDICATE if adjudicating else POWERS[OPEN]
        eligible = qualified_pool(data, policy, pool_power, excluded)
        if action == DEFER:
            core = {r["member_id"] for r in trace if r["reason"] in {"APPELLANT", "CHALLENGED_SIGNER"}}
            require(ctx["authenticated_member_id"] not in core, "ERR_SOD_CONFLICT_OF_INTEREST", "/server_context/authenticated_member_id")
            require(not ctx["collective_member_attestations"], "ERR_REGISTRAR_PATH_REQUIRES_NO_PANEL", "/server_context/collective_member_attestations")
            require(len(eligible) < quorum, "ERR_SOD_DEFERRAL_NOT_JUSTIFIED", "/server_context/reviewer_pool_snapshot")
            require(now < p["review_deadline_at"] <= scope["effective_until"] and p["pool_evidence_ref"] == ctx["reviewer_pool_snapshot"]["source_ref"], "ERR_DEFERRAL_DEADLINE_OR_EVIDENCE", "/command/payload")
        else:
            atts = ctx["collective_member_attestations"]; requested = p["panel_principal_ids"]
            require(len(requested) == len(set(requested)) and len(atts) == len(requested) and {m["principal_id"] for m in atts} == set(requested) and ctx["authenticated_principal_id"] in requested, "ERR_COLLECTIVE_ATTESTATIONS_REQUIRED", "/server_context/collective_member_attestations")
            panel = [m["member_id"] for m in atts]
            require(len(set(panel)) == len(panel), "ERR_DUPLICATE_PANEL_MEMBER", "/server_context/collective_member_attestations")
            for m in atts:
                require(member(ctx, m["principal_id"]) == m["member_id"], "ERR_SOD_IDENTITY_UNMAPPED", "/server_context/collective_member_attestations")
                snapshot, mr = m["authority_snapshot"], m["member_auth_receipt"]
                require(snapshot in ctx["authority_snapshots"], "ERR_MEMBER_RECEIPT_BINDING", "/server_context/collective_member_attestations")
                ms = authority_status(data, m["principal_id"], snapshot["mandate_id"], POWERS[action], snapshot["version_hash"])
                expected = {"principal_id": m["principal_id"], "member_id": m["member_id"], "mandate_id": snapshot["mandate_id"], "evaluated_version_hash": snapshot["version_hash"], "requested_power": POWERS[action], "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"], "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "checked_at": now, "verification_status": ms}
                require(mr == expected, "ERR_MEMBER_RECEIPT_BINDING", "/server_context/collective_member_attestations")
                require(ms == "VERIFIED_ACTIVE", STATUS_CODES.get(ms, "ERR_UNAUTHORIZED_POWER"), "/server_context/collective_member_attestations")
                consent = {"status": "CONFIRMED", "consented_payload_sha256": digest(p), "evaluated_target_version_seq": cmd["target_expected_version_seq"], "commitment_terms_version_seq": 1 if action == APPROVE else None, "case_rule_version_seq": policy["case_rule_version_seq"], "policy_version_seq": policy["version_seq"]}
                require(m["consent_binding"] == consent, "ERR_COLLECTIVE_CONSENT_MISMATCH", "/server_context/collective_member_attestations")
            require(not set(panel) & excluded, "ERR_SOD_CONFLICT_OF_INTEREST", "/server_context/collective_member_attestations")
            require(set(panel) <= eligible and len(panel) >= quorum, "ERR_REVIEW_PANEL_INELIGIBLE_OR_NO_QUORUM", "/server_context/collective_member_attestations")
            if action == RULE: require(set(panel) == set(target["review_panel_member_ids"]), "ERR_REVIEW_PANEL_CHANGED", "/command/payload/panel_principal_ids")
        if action == ADJUDICATE:
            ds = p["required_remedy_directives"]
            require((p["outcome"] == "UPHELD_REMEDY_REQUIRED") == bool(ds) and all(d["target_object_id"] in objects and objects[d["target_object_id"]]["case_id"] == ctx["case_id"] for d in ds), "ERR_REMEDY_DIRECTIVE_MISMATCH", "/command/payload/required_remedy_directives")
        if action == APPROVE:
            n = p["new_commitment"]; pool = objects[n["budget_pool_id"]]; phases = n["phases"]
            require(pool in domain["operations"]["resource_pools"] and pool["resource_spec"] == n["resource_spec"] == scope["resource_spec"] and n["total_obligated_minor"] <= scope["max_amount_minor"], "ERR_RESOURCE_SCOPE_MISMATCH", "/command/payload/new_commitment")
            require(pool["committed_obligations_minor"] + n["total_obligated_minor"] <= pool["total_capacity_minor"], "ERR_RESOURCE_POOL_EXHAUSTED", "/command/payload/new_commitment")
            require([p["phase_seq"] for p in phases] == list(range(1, len(phases) + 1)) and sum(p["allocated_minor"] for p in phases) == n["total_obligated_minor"] and {r["ticket_id"] for r in n["ticket_executor_member_ids"]} == {p["ticket_id"] for p in phases} and len(n["ticket_executor_member_ids"]) == len(phases), "ERR_PHASE_LEDGER_MISMATCH", "/command/payload/new_commitment")
            ids = [n["commitment_id"], ctx["new_envelope_id"] + ":operational"] + [p["ticket_id"] for p in phases]
            require(len(ids) == len(set(ids)) and not set(ids) & (set(objects) | set(envelope_index(domain))), "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/new_commitment")
            require(not b.evaluate_protective_orders(domain["operations"], ctx["case_id"], b.APPROVE, now), "ERR_BLOCKED_BY_PROTECTIVE_ORDER", "/domain_objects/operations/protective_orders")
    except ContractFailure as error:
        return [error.report["error_code"]], trace, panel, eligible
    return [], trace, panel, eligible


def expected_applied_domain(data, policy, trace, panel, eligible):
    domain = copy.deepcopy(data["before"]["domain_objects"]); cmd, ctx = data["command"], data["server_context"]
    action, p, eid = cmd["requested_action"], cmd["payload"], ctx["new_envelope_id"]
    target = object_index(domain)[cmd["target_object_id"]]; versions = []
    if action == SUBMIT:
        appeal = {k: copy.deepcopy(p[k]) for k in ("challenged_object_kind", "challenged_object_id", "challenged_object_version_seq", "challenged_decision_envelope_id", "raw_statement_text")}
        appeal.update(object_id=p["appeal_id"], kind="APPEAL", tenant_node_id=ctx["tenant_node_id"], case_id=ctx["case_id"], parent_object_id=ctx["case_id"], version_seq=1, appeal_status="SUBMITTED", appellant_member_ids=[ctx["authenticated_member_id"]], review_panel_member_ids=[], opening_envelope_id=None, admissibility_record=None, merits_adjudication_record=None, sod_deferral_record=None)
        domain["appeals"].append(appeal); versions = [{"object_id": appeal["object_id"], "version_seq": 1}]
        return domain, versions
    if action == APPROVE:
        translated = copy.deepcopy(data)
        translated["before"]["domain_objects"] = domain["operations"]
        translated["command"]["requested_action"] = b.APPROVE
        del translated["command"]["payload"]["panel_principal_ids"]
        translated["server_context"]["new_envelope_id"] = eid + ":operational"
        domain["operations"], versions = b.expected_applied_domain(translated)
    else:
        target["version_seq"] += 1; versions = [{"object_id": target["object_id"], "version_seq": target["version_seq"]}]
        if action == OPEN: target.update(appeal_status="ADMISSIBILITY_REVIEW", review_panel_member_ids=sorted(panel), opening_envelope_id=eid)
        elif action == RULE:
            target["appeal_status"] = "ADJUDICATING" if p["outcome"] == "ACCEPTED" else "RESOLVED"
            target["admissibility_record"] = {"outcome": p["outcome"], "panel_member_ids": sorted(panel), "rationale": p["rationale"], "decision_envelope_id": eid}
        elif action == ADJUDICATE:
            target["appeal_status"] = "RESOLVED"
            target["merits_adjudication_record"] = {"outcome": p["outcome"], "panel_member_ids": sorted(panel), "rationale": p["rationale"], "required_remedy_directives": copy.deepcopy(p["required_remedy_directives"]), "decision_envelope_id": eid}
        else:
            target["sod_deferral_record"] = {"deferral_action": p["deferral_action"], "review_deadline_at": p["review_deadline_at"], "eligible_reviewer_count": len(eligible), "required_quorum_count": policy["quorums"]["adjudication" if target["appeal_status"] == "ADJUDICATING" else "admissibility"], "pool_evidence_ref": copy.deepcopy(p["pool_evidence_ref"]), "scope_spec": copy.deepcopy(p["effectiveness_envelope_input"]["scope_spec"]), "remedy_spec": copy.deepcopy(p["effectiveness_envelope_input"]["remedy_spec"]), "decision_envelope_id": eid}
    inp = p["effectiveness_envelope_input"]
    envelope = {"envelope_id": eid, "action_type": action, "target_object_id": cmd["target_object_id"], "evaluated_target_version_seq": cmd["target_expected_version_seq"], "resulting_versions": versions, "mandate_ref": {"mandate_id": cmd["mandate_claim"]["mandate_id"], "version_hash": cmd["mandate_claim"]["expected_version_hash"], "clause_reference": inp["mandate_clause_ref"]}, "auth_check_receipt": copy.deepcopy(ctx["generated_auth_receipt"]), "evidence_refs": copy.deepcopy(inp["evidence_refs"]), "scope_spec": copy.deepcopy(inp["scope_spec"]), "remedy_spec": copy.deepcopy(inp["remedy_spec"]), "effect_parameters_sha256": digest(p), "actor_member_id": ctx["authenticated_member_id"], "collective_member_attestations": copy.deepcopy(ctx["collective_member_attestations"]), "payload_snapshot": copy.deepcopy(p), "policy_snapshot": copy.deepcopy(policy), "recusal_trace": trace, "projected_operational_envelope_id": eid + ":operational" if action == APPROVE else None}
    domain["procedure_envelopes"].append(envelope)
    return domain, versions


def validate_semantics(data):
    objects, target, policy = validate_basis(data)
    before, cmd, ctx, after = data["before"], data["command"], data["server_context"], data["after"]
    out = after["execution_outcome"]; observation = ctx["execution_observation"]
    if observation != "NORMAL":
        unknown = observation == "COMMIT_RESPONSE_LOST"
        require(ctx["generated_auth_receipt"] is None and out["auth_check_receipt"] is None and out["command_result"] is None and out["command_rejection"] is None and out["audit_event"] is None, "ERR_FAULT_CLAIMS_COMPLETED_AUTH", "/after/execution_outcome")
        require(out["outcome"] == ("OUTCOME_UNKNOWN" if unknown else "SYSTEM_FAULT") and after["domain_objects"] == (None if unknown else before["domain_objects"]), "ERR_FAULT_CLAIMS_DOMAIN_STATE", "/after")
        require(out["command_record"] == (None if unknown else before["command_record"]) and out["command_attempt"] == (None if unknown else before["command_attempt"]), "ERR_FAULT_REWRITES_COMMAND_STATE", "/after/execution_outcome")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "observed_at": ctx["evaluated_at"], "kind": observation, "recovery_action": "RECONCILE_REQUIRED" if unknown else "RETRY_AFTER_RECONCILIATION", "persistence_claim": "NOT_ASSERTED"}
        require(out["fault_observation"] == expected, "ERR_FAULT_OBSERVATION_MISMATCH", "/after/execution_outcome/fault_observation")
        return
    status = current_receipt(data)
    codes, trace, panel, eligible = decision_diagnostics(data, objects, target, policy, status)
    expected_status = "REJECTED" if codes else "APPLIED"
    require(out["outcome"] == expected_status, "ERR_DECLARED_OUTCOME_MISMATCH", "/after/execution_outcome/outcome")
    if codes:
        domain, versions = before["domain_objects"], []
        require(after["domain_objects"] == domain, "ERR_REJECTED_DOMAIN_MUTATION", "/after/domain_objects")
        expected = {"command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "rejection_error_code": codes[0], "diagnostic_codes": codes, "blocking_protective_order_ids": b.evaluate_protective_orders(domain["operations"], ctx["case_id"], b.APPROVE, ctx["evaluated_at"]) if codes == ["ERR_BLOCKED_BY_PROTECTIVE_ORDER"] else []}
        require(out["command_rejection"] == expected, "ERR_REJECTION_DIAGNOSTIC_MISMATCH", "/after/execution_outcome/command_rejection")
    else:
        domain, versions = expected_applied_domain(data, policy, trace, panel, eligible)
        require(out["command_rejection"] is None and after["domain_objects"] == domain, "ERR_WRITE_SET_VIOLATION", "/after/domain_objects")
        validate_domain(domain)
    require(out["auth_check_receipt"] == ctx["generated_auth_receipt"] and out["fault_observation"] is None, "ERR_RECEIPT_BINDING_MISMATCH", "/after/execution_outcome")
    retry = codes == ["ERR_MANDATE_SOURCE_UNAVAILABLE"]
    result = out["command_result"]
    require(result is not None, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
    expected = {"result_id": result["result_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "status": expected_status, "retry_disposition": "NEW_ATTEMPT_SAME_KEY_AFTER_RECOVERY" if retry else "FINAL", "applied_versions": versions}
    require(result == expected, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
    record, attempt = copy.deepcopy(before["command_record"]), copy.deepcopy(before["command_attempt"])
    record.update(version_seq=record["version_seq"] + 1, status="IN_FLIGHT" if retry else expected_status, result_id=result["result_id"])
    attempt.update(version_seq=attempt["version_seq"] + 1, status="FAILED_RETRYABLE" if retry else expected_status)
    require(out["command_record"] == record and out["command_attempt"] == attempt, "ERR_COMMAND_RESULT_BINDING_MISMATCH", "/after/execution_outcome")
    audit = out["audit_event"]
    require(audit is not None, "ERR_AUDIT_BINDING_MISMATCH", "/after/execution_outcome/audit_event")
    expected = {"audit_id": audit["audit_id"], "command_id": cmd["command_id"], "attempt_id": ctx["attempt_id"], "generation": ctx["generation"], "principal_id": ctx["authenticated_principal_id"], "action_id": cmd["requested_action"], "outcome": expected_status, "checked_at": ctx["evaluated_at"], "before_domain_sha256": digest(before["domain_objects"]), "after_domain_sha256": digest(domain), "written_versions": versions}
    require(audit == expected, "ERR_AUDIT_BINDING_MISMATCH", "/after/execution_outcome/audit_event")


def validate_transition(data):
    registry = check_registry()
    if not registry["is_valid"]: return registry
    for validator in (COMMON, VALIDATOR):
        error = b.a.schema_error(validator, data)
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
