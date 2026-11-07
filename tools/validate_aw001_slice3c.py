"""Slice 3C offline synthetic decision-round, secret ballot and audit isolation.

Validation only: never generates randomness, never tallies real votes, never
starts the application, and does not prove cryptographic unlinkability.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import validate_aw001_slice2b as b

report, require, digest, ContractFailure = b.report, b.require, b.digest, b.ContractFailure
load_json, FORMATS = b.load_json, b.FORMATS
SPEC = ROOT / "specs/aw001_slice3/3c"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

OPEN, CAST, CLOSE = "OPEN_DECISION_ROUND", "CAST_BALLOT", "CLOSE_DECISION_ROUND"
ACTIONS = (OPEN, CAST, CLOSE)
POWERS = {OPEN: "OPEN_DECISION_ROUND", CAST: "CAST_BALLOT", CLOSE: "CLOSE_DECISION_ROUND"}
PAYLOADS = {OPEN: "Payload0", CAST: "Payload1", CLOSE: "Payload2"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_ROUND_AND_ELIGIBILITY",
          "CHECK_SECRET_BALLOT_ISOLATION", "CHECK_ALLOWED_WRITE_SET",
          "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {OPEN: "CREATE_ROUND_AND_ELIGIBILITY",
          CAST: "APPEND_BALLOT_AND_CONSUME_ELIGIBILITY",
          CLOSE: "CLOSE_ROUND_AND_TALLY"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
NONCE = re.compile(r"^[0-9a-f]{32,64}$")
HMAC = re.compile(r"^hmac-sha256:[a-f0-9]{64}$")
CORE_COLLECTIONS = {"decision_rounds": "round_id", "voter_eligibility": "eligibility_id",
                    "ballots": "ballot_id",
                    "eligibility_audit_events": "audit_id",
                    "ballot_audit_events": "audit_id"}


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def raw_option_digest(round_id, option_code):
    return digest({"round_id": round_id, "option_code": option_code})


def eligibility_id(round_id, member_id):
    return "elig-" + round_id + "-" + member_id


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
    require(value["contract_version"] == "0.4.0-rc1-slice3c",
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


# ---------------------------------------------------------------- helpers
def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


def fingerprint_checks(round_id, option_code, fingerprint, existing_ballots):
    require(NONCE.match(fingerprint["nonce"]) is not None,
            "ERR_BALLOT_NONCE_INVALID", "/fingerprint/nonce")
    require(HMAC.match(fingerprint["hmac_sha256"]) is not None,
            "ERR_BALLOT_NONCE_INVALID", "/fingerprint/hmac_sha256")
    require(all(row["fingerprint"]["nonce"] != fingerprint["nonce"]
                for row in existing_ballots), "ERR_BALLOT_NONCE_REUSED",
            "/fingerprint/nonce")
    require(fingerprint["hmac_sha256"] != raw_option_digest(round_id, option_code).replace(
        "sha256:", "hmac-sha256:"), "ERR_BALLOT_RAW_HASH_FORBIDDEN",
        "/fingerprint/hmac_sha256")


def tally_for(round_row, ballots, eligibility):
    round_ballots = [row for row in ballots if row["round_id"] == round_row["round_id"]]
    counts = [{"option_code": option["option_code"],
               "count": sum(1 for row in round_ballots
                            if row["option_code"] == option["option_code"])}
              for option in round_row["options"]]
    consumed = [row for row in eligibility
                if row["round_id"] == round_row["round_id"] and row["status"] == "CONSUMED"]
    return {"counts": counts, "ballots_cast": len(round_ballots),
            "eligible_count": len(round_row["eligible_member_ids"]),
            "consumed_count": len(consumed)}


# ---------------------------------------------------------------- domain
def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    rounds = {row["round_id"]: row for row in domain["decision_rounds"]}
    require(len(rounds) == len(domain["decision_rounds"]), "ERR_DUPLICATE_OBJECT_ID",
            "/decision_rounds")
    ids = []
    for name, key in CORE_COLLECTIONS.items():
        rows = [row[key] for row in domain[name]]
        require(len(rows) == len(set(rows)), "ERR_DUPLICATE_OBJECT_ID", "/" + name)
        ids += rows
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
    referenced = set()
    eligibility_by_key = {(row["round_id"], row["member_id"]): row
                          for row in domain["voter_eligibility"]}
    for row in domain["decision_rounds"]:
        require(len({o["option_code"] for o in row["options"]}) == len(row["options"]),
                "ERR_ROUND_OPTION_INVALID", "/decision_rounds")
        require(row["version_seq"] == (2 if row["round_status"] == "CLOSED" else 1),
                "ERR_ROUND_LEDGER_INCONSISTENT", "/decision_rounds")
        if row["round_status"] == "OPEN":
            require(row["tally"] is None and row["closed_envelope_id"] is None,
                    "ERR_ROUND_LEDGER_INCONSISTENT", "/decision_rounds")
        else:
            require(row["tally"] == tally_for(row, domain["ballots"],
                                              domain["voter_eligibility"]),
                    "ERR_ROUND_TALLY_MISMATCH", "/decision_rounds")
            creation = envelopes.get(row["closed_envelope_id"])
            require(creation is not None and creation["action_type"] == CLOSE
                    and creation["target_object_id"] == row["round_id"],
                    "ERR_INVALID_EFFECT_HISTORY", "/decision_rounds")
            referenced.add(row["closed_envelope_id"])
        opened = envelopes.get(row["opened_envelope_id"])
        require(opened is not None and opened["action_type"] == OPEN
                and {"object_id": row["round_id"], "version_seq": 1}
                in opened["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/decision_rounds")
        referenced.add(row["opened_envelope_id"])
    for row in domain["voter_eligibility"]:
        round_row = rounds.get(row["round_id"])
        require(round_row is not None and row["member_id"]
                in round_row["eligible_member_ids"], "ERR_ELIGIBILITY_LEDGER_INCONSISTENT",
                "/voter_eligibility")
        if row["status"] == "UNUSED":
            require(row["version_seq"] == 1 and row["cast_command_id"] is None
                    and row["cast_receipt_id"] is None and row["cast_envelope_id"] is None,
                    "ERR_ELIGIBILITY_LEDGER_INCONSISTENT", "/voter_eligibility")
        else:
            require(row["version_seq"] == 2 and row["cast_command_id"] is not None
                    and row["cast_receipt_id"] is not None
                    and row["cast_envelope_id"] is not None,
                    "ERR_ELIGIBILITY_LEDGER_INCONSISTENT", "/voter_eligibility")
            cast_envelope = envelopes.get(row["cast_envelope_id"])
            require(cast_envelope is not None and cast_envelope["action_type"] == CAST
                    and cast_envelope["target_object_id"] == row["round_id"]
                    and {"object_id": row["eligibility_id"], "version_seq": 2}
                    in cast_envelope["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                    "/voter_eligibility")
            referenced.add(row["cast_envelope_id"])
    for row in domain["ballots"]:
        round_row = rounds.get(row["round_id"])
        require(round_row is not None
                and row["option_code"] in {o["option_code"] for o in round_row["options"]},
                "ERR_BALLOT_OPTION_INVALID", "/ballots")
        require(UUID4.match(row["ballot_id"]) is not None, "ERR_BALLOT_ID_NOT_UUID4",
                "/ballots")
        fingerprint_checks(row["round_id"], row["option_code"], row["fingerprint"],
                           [other for other in domain["ballots"]
                            if other["ballot_id"] != row["ballot_id"]])
    for event in domain["ballot_audit_events"]:
        require(any(row["ballot_id"] == event["ballot_id"]
                    and row["fingerprint"] == event["fingerprint"]
                    and row["round_id"] == event["round_id"]
                    for row in domain["ballots"]),
                "ERR_SECRET_BALLOT_ISOLATION", "/ballot_audit_events")
    for event in domain["eligibility_audit_events"]:
        eligibility = eligibility_by_key.get((event["round_id"], event["member_id"]))
        require(eligibility is not None and eligibility["status"] == "CONSUMED"
                and eligibility["eligibility_id"] == event["eligibility_id"]
                and eligibility["cast_command_id"] == event["command_id"]
                and eligibility["cast_receipt_id"] == event["receipt_id"],
                "ERR_SECRET_BALLOT_ISOLATION", "/eligibility_audit_events")
    for row in domain["ballots"]:
        require(sum(1 for event in domain["ballot_audit_events"]
                    if event["ballot_id"] == row["ballot_id"]) == 1,
                "ERR_SECRET_BALLOT_ISOLATION", "/ballot_audit_events")
    for row in domain["voter_eligibility"]:
        if row["status"] == "CONSUMED":
            require(sum(1 for event in domain["eligibility_audit_events"]
                        if event["eligibility_id"] == row["eligibility_id"]) == 1,
                    "ERR_SECRET_BALLOT_ISOLATION", "/eligibility_audit_events")
    for envelope in domain["effectiveness_envelopes"]:
        require(envelope["action_type"] in ACTIONS
                and envelope["effect_parameters_sha256"] == digest(envelope["payload_snapshot"]),
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    require(referenced == set(envelopes), "ERR_INVALID_EFFECT_HISTORY",
            "/effectiveness_envelopes")
    return object_index(domain)


def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"],
                                  "kind": "PROTECTIVE_ORDER"}
    for row in domain["decision_rounds"]:
        index[row["round_id"]] = row
    return index


# ---------------------------------------------------------------- basis
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
    expected_kind = "CASE" if cmd["requested_action"] == OPEN else "ROUND"
    kind = "CASE" if target.get("kind") == "CASE" else (
        "ROUND" if "round_id" in target else None)
    require(kind == expected_kind, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
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
                "current_attempt_id": ctx["attempt_id"], "generation": ctx["generation"]}
    require(record["status"] == attempt["status"] == "IN_FLIGHT" and record["result_id"] is None
            and all(record[k] == v for k, v in expected.items()),
            "ERR_COMMAND_BINDING_MISMATCH", "/before/command_record")
    bare = digest(cmd)
    if cmd["requested_action"] == CAST:
        require(record["request_sha256"].startswith("hmac-sha256:")
                and record["request_sha256"] != bare,
                "ERR_BALLOT_REQUEST_DIGEST_UNSAFE", "/before/command_record/request_sha256")
    else:
        require(record["request_sha256"] == bare, "ERR_COMMAND_BINDING_MISMATCH",
                "/before/command_record/request_sha256")
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
        if action == OPEN:
            _diagnose_open(data, objects)
        elif action == CAST:
            _diagnose_cast(data, target)
        else:
            _diagnose_close(data, target)
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


def _diagnose_open(data, objects):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(payload["round_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/round_id")
    require(len({row["option_code"] for row in payload["options"]}) == len(payload["options"]),
            "ERR_ROUND_OPTION_INVALID", "/command/payload/options")
    require(len(set(payload["eligible_member_ids"])) == len(payload["eligible_member_ids"]),
            "ERR_ELIGIBILITY_LEDGER_INCONSISTENT", "/command/payload/eligible_member_ids")
    known = {row["member_id"] for row in ctx["member_directory"]
             if row["status"] == "VERIFIED"}
    require(set(payload["eligible_member_ids"]) <= known, "ERR_VOTER_NOT_ELIGIBLE",
            "/command/payload/eligible_member_ids")
    require(payload["closes_at"] > ctx["evaluated_at"], "ERR_ROUND_CLOSED",
            "/command/payload/closes_at")


def _diagnose_cast(data, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    domain = data["before"]["domain_objects"]
    require(target["round_status"] == "OPEN", "ERR_ROUND_NOT_OPEN", "/round_status")
    require(ctx["evaluated_at"] < target["closes_at"], "ERR_ROUND_CLOSED", "/closes_at")
    member_id = ctx["authenticated_member_id"]
    require(member_id in target["eligible_member_ids"], "ERR_VOTER_NOT_ELIGIBLE",
            "/domain_objects/voter_eligibility")
    option_codes = {row["option_code"] for row in target["options"]}
    require(payload["option_code"] in option_codes, "ERR_BALLOT_OPTION_INVALID",
            "/command/payload/option_code")
    require(UUID4.match(payload["ballot_id"]) is not None, "ERR_BALLOT_ID_NOT_UUID4",
            "/command/payload/ballot_id")
    require(all(row["ballot_id"] != payload["ballot_id"] for row in domain["ballots"]),
            "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/ballot_id")
    eligibility = next((row for row in domain["voter_eligibility"]
                        if row["round_id"] == target["round_id"]
                        and row["member_id"] == member_id), None)
    require(eligibility is not None, "ERR_ELIGIBILITY_LEDGER_INCONSISTENT",
            "/domain_objects/voter_eligibility")
    require(eligibility["status"] == "UNUSED", "ERR_DUPLICATE_BALLOT",
            "/domain_objects/voter_eligibility")
    fingerprint_checks(target["round_id"], payload["option_code"], payload["fingerprint"],
                       domain["ballots"])


def _diagnose_close(data, target):
    ctx = data["server_context"]
    domain = data["before"]["domain_objects"]
    require(target["round_status"] == "OPEN", "ERR_ROUND_ALREADY_CLOSED", "/round_status")
    require(ctx["evaluated_at"] >= target["closes_at"], "ERR_ROUND_NOT_CLOSED",
            "/closes_at")
    tally = tally_for(target, domain["ballots"], domain["voter_eligibility"])
    require(tally["ballots_cast"] == tally["consumed_count"],
            "ERR_ROUND_TALLY_MISMATCH", "/domain_objects/voter_eligibility")


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
    if action == OPEN:
        round_row = {"round_id": payload["round_id"],
                     "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"],
                     "version_seq": 1, "round_status": "OPEN",
                     "options": copy.deepcopy(payload["options"]),
                     "eligible_member_ids": copy.deepcopy(payload["eligible_member_ids"]),
                     "closes_at": payload["closes_at"], "tally": None,
                     "created_command_id": cmd["command_id"],
                     "opened_envelope_id": ctx["new_envelope_id"],
                     "closed_envelope_id": None}
        eligibility = [{"eligibility_id": eligibility_id(payload["round_id"], member_id),
                        "round_id": payload["round_id"], "member_id": member_id,
                        "version_seq": 1, "status": "UNUSED", "cast_command_id": None,
                        "cast_receipt_id": None, "cast_envelope_id": None}
                       for member_id in payload["eligible_member_ids"]]
        domain["decision_rounds"].append(round_row)
        domain["voter_eligibility"].extend(eligibility)
        versions = [{"object_id": round_row["round_id"], "version_seq": 1}]
        versions += [{"object_id": row["eligibility_id"], "version_seq": 1}
                     for row in eligibility]
        envelope_versions = versions
    elif action == CAST:
        round_row = next(row for row in domain["decision_rounds"]
                         if row["round_id"] == cmd["target_object_id"])
        member_id = ctx["authenticated_member_id"]
        eligibility = next(row for row in domain["voter_eligibility"]
                           if row["round_id"] == round_row["round_id"]
                           and row["member_id"] == member_id)
        ballot = {"ballot_id": payload["ballot_id"], "round_id": round_row["round_id"],
                  "option_code": payload["option_code"],
                  "fingerprint": copy.deepcopy(payload["fingerprint"])}
        domain["ballots"].append(ballot)
        eligibility.update(version_seq=2, status="CONSUMED",
                           cast_command_id=cmd["command_id"],
                           cast_receipt_id=ctx["generated_auth_receipt"]["receipt_id"],
                           cast_envelope_id=ctx["new_envelope_id"])
        domain["eligibility_audit_events"].append({
            "audit_id": "eligibility-audit-" + cmd["command_id"],
            "action_id": CAST, "round_id": round_row["round_id"],
            "eligibility_id": eligibility["eligibility_id"], "member_id": member_id,
            "principal_id": ctx["authenticated_principal_id"],
            "command_id": cmd["command_id"],
            "receipt_id": ctx["generated_auth_receipt"]["receipt_id"],
            "checked_at": ctx["evaluated_at"]})
        domain["ballot_audit_events"].append({
            "audit_id": "ballot-audit-" + cmd["command_id"],
            "action_id": CAST, "round_id": round_row["round_id"],
            "ballot_id": ballot["ballot_id"],
            "fingerprint": copy.deepcopy(ballot["fingerprint"])})
        envelope_versions = [{"object_id": eligibility["eligibility_id"], "version_seq": 2}]
        versions = []
    else:
        round_row = next(row for row in domain["decision_rounds"]
                         if row["round_id"] == cmd["target_object_id"])
        round_row.update(version_seq=2, round_status="CLOSED",
                         tally=tally_for(round_row, domain["ballots"],
                                         domain["voter_eligibility"]),
                         closed_envelope_id=ctx["new_envelope_id"])
        versions = [{"object_id": round_row["round_id"], "version_seq": 2}]
        envelope_versions = versions
    domain["effectiveness_envelopes"].append(envelope(data, action, envelope_versions))
    return domain, versions


def identity_safe_response(data):
    if data["command"]["requested_action"] != CAST:
        return None
    return {"round_id": data["command"]["target_object_id"],
            "eligibility_status": "BALLOT_CAST"}


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
                "applied_versions": versions,
                "identity_safe_response": None if expected_status == "REJECTED"
                else identity_safe_response(data)}
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
                "after_domain_sha256": digest(domain),
                "written_versions": versions if cmd["requested_action"] != CAST else []}
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
