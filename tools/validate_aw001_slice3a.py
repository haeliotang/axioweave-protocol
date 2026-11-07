"""Slice 3A offline synthetic participation-audit and speaker-statement contracts.

Validation only: never starts the application, never opens ballots, never
resolves identities, and never asserts real-world participation or speech.
"""

import argparse
import copy
import hashlib
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
SPEC = ROOT / "specs/aw001_slice3/3a"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

RECORD_AUDIT, ESCALATE, PROCEED, RECORD_STATEMENT, CONFIRM, WITHDRAW, RULE = (
    "RECORD_INCLUSION_AUDIT", "ESCALATE_INCLUSION_SHORTFALL", "PROCEED_WITH_DISCLOSURE",
    "RECORD_SPEAKER_STATEMENT", "CONFIRM_STATEMENT_EXPRESSION",
    "WITHDRAW_STATEMENT_PERMISSION", "RULE_BALLOT_MATERIALITY",
)
ACTIONS = (RECORD_AUDIT, ESCALATE, PROCEED, RECORD_STATEMENT, CONFIRM, WITHDRAW, RULE)
POWERS = {RECORD_AUDIT: "RECORD_INCLUSION_AUDIT",
          ESCALATE: "ESCALATE_INCLUSION_SHORTFALL",
          PROCEED: "DECIDE_PROCEDURAL_GAP_DISCLOSURE",
          RULE: "RULE_BALLOT_MATERIALITY",
          RECORD_STATEMENT: None, CONFIRM: None, WITHDRAW: None}
PAYLOADS = {RECORD_AUDIT: "Payload0", ESCALATE: "Payload1", PROCEED: "Payload2",
            RECORD_STATEMENT: "Payload3", CONFIRM: "Payload4", WITHDRAW: "Payload5",
            RULE: "Payload6"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_PARTICIPATION_LEDGER",
          "CHECK_STATEMENT_VERSION_CHAIN", "CHECK_ALLOWED_WRITE_SET",
          "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {RECORD_AUDIT: "CREATE_INCLUSION_AUDIT", ESCALATE: "APPEND_ESCALATION",
          PROCEED: "APPEND_DISCLOSURE", RECORD_STATEMENT: "CREATE_SPEAKER_STATEMENT",
          CONFIRM: "APPEND_EXPRESSION_CONFIRMATION",
          WITHDRAW: "APPEND_DISPLAY_WITHDRAWAL", RULE: "CREATE_MATERIALITY_RULING"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
CORE_GAP_CODES = {"UNKNOWN_ELIGIBLE_POPULATION", "INTERVAL_ELIGIBLE_POPULATION",
                  "BELOW_REQUIRED_FLOOR"}
NON_CORE_GAP_CODES = {"LATE_RESPONSES_UNRESOLVED", "SUPPORT_MEASURES_INCOMPLETE",
                      "PARTIAL_STRATA_DISCLOSED"}
DISPLAY_PERMISSIONS = {"PUBLIC_PERMITTED", "RESTRICTED_REVIEWERS", "PRIVATE_NO_DISPLAY"}
POPULATION_KINDS = {"EXACT", "INTERVAL", "UNKNOWN"}


def text_digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    if set(value) != {"contract_version", "global_lock_order", "actions"}:
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/")
    if value["contract_version"] != "0.4.0-rc1-slice3a":
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/contract_version")
    if value["global_lock_order"] != LOCK_ORDER:
        return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH", "/global_lock_order")
    if not isinstance(value["actions"], dict) or set(value["actions"]) != set(ACTIONS):
        return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", "/actions")
    for action in ACTIONS:
        entry = value["actions"][action]
        base = "/actions/" + action
        if not isinstance(entry, dict) or set(entry) != {
                "required_power", "requires_effectiveness_envelope", "required_validators",
                "lock_levels", "write_policy"}:
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", base)
        if (entry["required_power"] != POWERS[action]
                or entry["requires_effectiveness_envelope"] is not (POWERS[action] is not None)
                or entry["write_policy"] != WRITES[action]):
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", base)
        if entry["required_validators"] != CHECKS:
            if not isinstance(entry["required_validators"], list) or any(
                    check not in entry["required_validators"] for check in CHECKS):
                return report("REGISTRY_LOADER", "ERR_REGISTRY_MANDATORY_VALIDATOR_MISSING",
                              base + "/required_validators")
            return report("REGISTRY_LOADER", "ERR_ACTION_REGISTRY_VIOLATION", base)
        if entry["lock_levels"] != [0, 1, 2, 3, 4] or any(
                type(level) is not int for level in entry["lock_levels"]):
            return report("REGISTRY_LOADER", "ERR_REGISTRY_LOCK_ORDER_MISMATCH",
                          base + "/lock_levels")
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


# ---------------------------------------------------------------- indexing
def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"], "kind": "PROTECTIVE_ORDER"}
    for name, key in (("inclusion_audits", "audit_id"), ("speaker_statements", "statement_id"),
                      ("materiality_rulings", "ruling_id")):
        for row in domain[name]:
            index[row[key]] = row
    return index


def envelope_index(domain):
    return {row["envelope_id"]: row for row in domain["effectiveness_envelopes"]}


def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" and b.known_member(row["member_id"]) else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


# ---------------------------------------------------------------- participation
def population_denominator_kind(population):
    return population["kind"]


def events_consistent(events):
    if any(type(events[k]) is not int or events[k] < 0 for k in
           ("responded", "participated", "refused", "unreachable", "withdrew", "unknown_outcome")):
        return False
    if events["invited"] is not None:
        if type(events["invited"]) is not int or events["invited"] < 0:
            return False
        if events["responded"] + events["refused"] + events["unreachable"] + \
                events["withdrew"] + events["unknown_outcome"] != events["invited"]:
            return False
    return events["participated"] <= events["responded"]


def derive_inclusion_status(window, population, floor, events, strata, evaluated_at):
    reasons = []
    if not (window["start"] < window["end"]):
        raise ContractFailure("ERR_INCLUSION_WINDOW_INVALID", "/participation_window")
    for index, stratum in enumerate(strata):
        if not events_consistent(stratum["events"]):
            return "INDETERMINATE_INSUFFICIENT_DATA", {
                "computed_ratio_ppm": None, "denominator_kind": population["kind"],
                "reason_codes": ["EVENTS_INCOMPLETE"]}, None
    ratio = None
    status = "INDETERMINATE_INSUFFICIENT_DATA"
    if evaluated_at < window["end"]:
        status, reasons = "NOT_YET_EVALUATED", ["WINDOW_NOT_CLOSED"]
    elif not events_consistent(events):
        reasons = ["EVENTS_INCOMPLETE"]
    elif population["kind"] == "UNKNOWN":
        reasons = ["UNKNOWN_DENOMINATOR"]
    elif population["kind"] == "INTERVAL":
        reasons = ["INTERVAL_DENOMINATOR"]
    else:
        metric = events["participated"] if floor["metric"] == "PARTICIPATED" else events["responded"]
        ratio = metric * 1000000 // population["exact_count"]
        if ratio >= floor["ratio_min_ppm"]:
            status, reasons = "MET", ["MET"]
        else:
            status, reasons = "BELOW_REQUIRED_FLOOR", ["BELOW_FLOOR"]
    if status != "MET" and not reasons:
        reasons = ["INDETERMINATE"]
    return status, {"computed_ratio_ppm": ratio,
                    "denominator_kind": population_denominator_kind(population),
                    "reason_codes": reasons}, ratio


def derive_gate_disposition(status, disclosures):
    if status == "MET":
        return "MET"
    return "PROCEED_WITH_DISCLOSURE" if disclosures else "BLOCKED"


def validate_inclusion(audit, envelopes):
    window, population = audit["participation_window"], audit["eligible_population"]
    if population["kind"] == "INTERVAL" and not population["lower_bound"] < population["upper_bound"]:
        raise ContractFailure("ERR_INCLUSION_LEDGER_INCONSISTENT", "/eligible_population")
    if len({s["stratum_id"] for s in audit["strata"]}) != len(audit["strata"]):
        raise ContractFailure("ERR_INCLUSION_LEDGER_INCONSISTENT", "/strata")
    status, basis, _ = derive_inclusion_status(window, population, audit["floor"],
                                               audit["events"], audit["strata"], audit["evaluated_at"])
    require(audit["audit_status"] == status and audit["status_basis"] == basis,
            "ERR_INCLUSION_STATUS_MISMATCH", "/audit_status")
    if status in {"BELOW_REQUIRED_FLOOR", "INDETERMINATE_INSUFFICIENT_DATA"}:
        require(isinstance(audit["review_deadline_at"], str)
                and audit["review_deadline_at"] > audit["evaluated_at"]
                and b.known_member(audit["review_owner_member_id"]),
                "ERR_INCLUSION_REVIEW_NOT_ASSIGNED", "/review_deadline_at")
    else:
        require(audit["review_deadline_at"] is None and audit["review_owner_member_id"] is None,
                "ERR_INCLUSION_REVIEW_NOT_ASSIGNED", "/review_deadline_at")
    if status == "MET":
        require(not audit["disclosures"], "ERR_INVALID_EFFECT_HISTORY", "/disclosures")
    require(audit["gate_disposition"] == derive_gate_disposition(status, audit["disclosures"]),
            "ERR_INVALID_EFFECT_HISTORY", "/gate_disposition")
    require(audit["version_seq"] == 1 + len(audit["escalations"]) + len(audit["disclosures"]),
            "ERR_INVALID_EFFECT_HISTORY", "/version_seq")
    creation = envelopes.get(audit["created_envelope_id"])
    require(creation is not None and creation["action_type"] == RECORD_AUDIT
            and {"object_id": audit["audit_id"], "version_seq": 1} in creation["resulting_versions"],
            "ERR_INVALID_EFFECT_HISTORY", "/created_envelope_id")
    seen = set()
    recorded_at = []
    for escalation in audit["escalations"]:
        require(escalation["escalation_id"] not in seen, "ERR_DUPLICATE_OBJECT_ID",
                "/escalations")
        seen.add(escalation["escalation_id"])
        remediation, deadline = escalation["remediation_window"], escalation["review_deadline_at"]
        require(remediation["start"] < remediation["end"] and remediation["end"] <= deadline
                and deadline > audit["evaluated_at"], "ERR_INCLUSION_REMEDIATION_WINDOW_INVALID",
                "/escalations/" + escalation["escalation_id"])
        require(b.known_member(escalation["handler_member_id"]), "ERR_SOD_IDENTITY_UNMAPPED",
                "/escalations/" + escalation["escalation_id"] + "/handler_member_id")
        envelope = envelopes.get(escalation["envelope_id"])
        require(envelope is not None and envelope["action_type"] == ESCALATE
                and envelope["target_object_id"] == audit["audit_id"],
                "ERR_INVALID_EFFECT_HISTORY", "/escalations/" + escalation["escalation_id"] + "/envelope_id")
        recorded_at.append(escalation["recorded_at"])
    for disclosure in audit["disclosures"]:
        require(disclosure["disclosure_id"] not in seen, "ERR_DUPLICATE_OBJECT_ID", "/disclosures")
        seen.add(disclosure["disclosure_id"])
        require(set(disclosure["gap_codes"]) <= NON_CORE_GAP_CODES,
                "ERR_INCLUSION_CORE_GAP_NOT_WAIVABLE", "/disclosures")
        envelope = envelopes.get(disclosure["envelope_id"])
        require(envelope is not None and envelope["action_type"] == PROCEED
                and envelope["target_object_id"] == audit["audit_id"],
                "ERR_INVALID_EFFECT_HISTORY", "/disclosures/" + disclosure["disclosure_id"] + "/envelope_id")
        recorded_at.append(disclosure["recorded_at"])
    require(recorded_at == sorted(recorded_at), "ERR_INVALID_EFFECT_HISTORY", "/escalations")


# ---------------------------------------------------------------- statements
def latest_withdrawal(withdrawals, kind, translation_id):
    matches = [w for w in withdrawals if w["target_kind"] == kind
               and w["translation_id"] == translation_id]
    return matches[-1] if matches else None


def derive_display_state(statement):
    current = statement["current_original_version_seq"]
    original_version = statement["original_versions"][current - 1]
    original_withdrawal = latest_withdrawal(statement["display_withdrawals"], "ORIGINAL", None)
    if original_withdrawal is not None:
        original_scope = "MASKED"
    else:
        original_scope = {"PUBLIC_PERMITTED": "PUBLIC",
                          "RESTRICTED_REVIEWERS": "RESTRICTED",
                          "PRIVATE_NO_DISPLAY": "MASKED"}[original_version["display_permission"]]
    original_state = {"display_permission": original_version["display_permission"],
                      "display_scope": original_scope,
                      "withdrawal_id": original_withdrawal["withdrawal_id"] if original_withdrawal else None}
    translations = []
    for translation in statement["translations"]:
        version = translation["versions"][translation["current_version_seq"] - 1]
        withdrawal = latest_withdrawal(statement["display_withdrawals"], "TRANSLATION",
                                       translation["translation_id"])
        if withdrawal is not None:
            if original_scope == "PUBLIC":
                scope, fallback = "ORIGINAL_FALLBACK", current
            else:
                scope, fallback = "MASKED", None
        else:
            scope, fallback = {"PUBLIC_PERMITTED": "PUBLIC",
                               "RESTRICTED_REVIEWERS": "RESTRICTED",
                               "PRIVATE_NO_DISPLAY": "MASKED"}[version["display_permission"]], None
        translations.append({"translation_id": translation["translation_id"],
                             "display_permission": version["display_permission"],
                             "display_scope": scope,
                             "fallback_original_version_seq": fallback,
                             "withdrawal_id": withdrawal["withdrawal_id"] if withdrawal else None})
    return {"original": original_state, "translations": translations}


def derive_representation(statement):
    result = []
    for translation in statement["translations"]:
        current = translation["current_version_seq"]
        version = translation["versions"][current - 1]
        confirmation = next((c for c in statement["expression_confirmations"]
                             if c["text_kind"] == "TRANSLATION"
                             and c["translation_id"] == translation["translation_id"]
                             and c["text_version_seq"] == current
                             and c["text_sha256"] == text_digest(version["text"])), None)
        result.append({"translation_id": translation["translation_id"],
                       "status": "CONFIRMED" if confirmation else "UNCONFIRMED_DRAFT",
                       "confirmed_text_version_seq": current if confirmation else None})
    return {"original_version_seq": statement["current_original_version_seq"],
            "translations": result}


def validate_statement(statement):
    versions = statement["original_versions"]
    require([v["version_seq"] for v in versions] == list(range(1, len(versions) + 1))
            and statement["current_original_version_seq"] == len(versions),
            "ERR_STATEMENT_VERSION_CHAIN", "/original_versions")
    translation_ids = [t["translation_id"] for t in statement["translations"]]
    require(len(set(translation_ids)) == len(translation_ids), "ERR_DUPLICATE_OBJECT_ID",
            "/translations")
    for translation in statement["translations"]:
        sequence = [v["version_seq"] for v in translation["versions"]]
        require(sequence == list(range(1, len(sequence) + 1))
                and translation["current_version_seq"] == len(sequence),
                "ERR_STATEMENT_VERSION_CHAIN", "/translations")
        for version in translation["versions"]:
            source_seq = version["source_original_version_seq"]
            require(1 <= source_seq <= len(versions), "ERR_STATEMENT_VERSION_CHAIN",
                    "/translations/" + translation["translation_id"])
            require(version["source_text_sha256"] == text_digest(versions[source_seq - 1]["text"]),
                    "ERR_CONFIRMATION_TEXT_MISMATCH",
                    "/translations/" + translation["translation_id"])
    confirmation_ids = [c["confirmation_id"] for c in statement["expression_confirmations"]]
    require(len(set(confirmation_ids)) == len(confirmation_ids), "ERR_DUPLICATE_OBJECT_ID",
            "/expression_confirmations")
    for confirmation in statement["expression_confirmations"]:
        require(confirmation["confirmed_by_member_id"] == statement["speaker_member_id"],
                "ERR_CONFIRMATION_SUBJECT_MISMATCH", "/expression_confirmations")
        if confirmation["text_kind"] == "ORIGINAL":
            require(confirmation["translation_id"] is None, "ERR_CONFIRMATION_TEXT_NOT_FOUND",
                    "/expression_confirmations")
            require(1 <= confirmation["text_version_seq"] <= len(versions),
                    "ERR_CONFIRMATION_TEXT_NOT_FOUND", "/expression_confirmations")
            expected = text_digest(versions[confirmation["text_version_seq"] - 1]["text"])
        else:
            translation = next((t for t in statement["translations"]
                                if t["translation_id"] == confirmation["translation_id"]), None)
            require(translation is not None, "ERR_CONFIRMATION_TEXT_NOT_FOUND",
                    "/expression_confirmations")
            require(1 <= confirmation["text_version_seq"] <= len(translation["versions"]),
                    "ERR_CONFIRMATION_TEXT_NOT_FOUND", "/expression_confirmations")
            expected = text_digest(translation["versions"][confirmation["text_version_seq"] - 1]["text"])
        require(confirmation["text_sha256"] == expected, "ERR_CONFIRMATION_TEXT_MISMATCH",
                "/expression_confirmations")
    withdrawal_ids = [w["withdrawal_id"] for w in statement["display_withdrawals"]]
    require(len(set(withdrawal_ids)) == len(withdrawal_ids), "ERR_DUPLICATE_OBJECT_ID",
            "/display_withdrawals")
    seen_targets = set()
    for withdrawal in statement["display_withdrawals"]:
        if withdrawal["target_kind"] == "ORIGINAL":
            require(withdrawal["translation_id"] is None, "ERR_DISPLAY_PERMISSION_NOT_FOUND",
                    "/display_withdrawals")
            target = ("ORIGINAL", None)
        else:
            translation = next((t for t in statement["translations"]
                                if t["translation_id"] == withdrawal["translation_id"]), None)
            require(translation is not None, "ERR_DISPLAY_PERMISSION_NOT_FOUND",
                    "/display_withdrawals")
            target = ("TRANSLATION", withdrawal["translation_id"])
        require(target not in seen_targets, "ERR_DISPLAY_PERMISSION_ALREADY_WITHDRAWN",
                "/display_withdrawals")
        seen_targets.add(target)
    require(statement["version_seq"] == 1 + len(statement["expression_confirmations"]) +
            len(statement["display_withdrawals"]), "ERR_STATEMENT_VERSION_CHAIN", "/version_seq")
    require(statement["display_state"] == derive_display_state(statement),
            "ERR_STATEMENT_VERSION_CHAIN", "/display_state")
    require(statement["representation"] == derive_representation(statement),
            "ERR_STATEMENT_VERSION_CHAIN", "/representation")


def validate_ruling(ruling, statements, envelopes):
    statement = statements.get(ruling["statement_id"])
    require(statement is not None, "ERR_MATERIALITY_RULING_SUBJECT_MISMATCH", "/statement_id")
    require(1 <= ruling["statement_version_seq"] <= statement["version_seq"],
            "ERR_MATERIALITY_RULING_SUBJECT_MISMATCH", "/statement_version_seq")
    envelope = envelopes.get(ruling["envelope_id"])
    require(envelope is not None and envelope["action_type"] == RULE
            and envelope["target_object_id"] == ruling["statement_id"],
            "ERR_INVALID_EFFECT_HISTORY", "/materiality_rulings")
    require(ruling["actor_member_id"] == envelope["actor_member_id"],
            "ERR_INVALID_EFFECT_HISTORY", "/materiality_rulings")


# ---------------------------------------------------------------- domain
def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    audit_ids = [a["audit_id"] for a in domain["inclusion_audits"]]
    statement_ids = [s["statement_id"] for s in domain["speaker_statements"]]
    ruling_ids = [r["ruling_id"] for r in domain["materiality_rulings"]]
    envelope_ids = [e["envelope_id"] for e in domain["effectiveness_envelopes"]]
    ids = audit_ids + statement_ids + ruling_ids + envelope_ids
    operation_ids = [row["object_id"] for name in
                     ("cases", "resource_pools", "commitments", "tickets")
                     for row in domain["operations"][name]]
    operation_ids += [o["order_id"] for o in domain["operations"]["protective_orders"]]
    operation_ids += [e["envelope_id"] for e in domain["operations"]["protection_envelopes"]]
    operation_ids += [e["envelope_id"] for e in domain["operations"]["effectiveness_envelopes"]]
    require(len(ids) == len(set(ids)) and not set(ids) & set(operation_ids),
            "ERR_DUPLICATE_OBJECT_ID", "/domain_objects")
    envelopes = envelope_index(domain)
    for envelope in domain["effectiveness_envelopes"]:
        action = envelope["action_type"]
        require(action in ACTIONS and POWERS[action] is not None, "ERR_INVALID_EFFECT_HISTORY",
                "/effectiveness_envelopes")
        require(envelope["effect_parameters_sha256"] == digest(envelope["payload_snapshot"]),
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
        require(validate_payload({"requested_action": action,
                                  "payload": envelope["payload_snapshot"]}) is None,
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
        for version in envelope["resulting_versions"]:
            target = object_index(domain).get(version["object_id"])
            require(target is not None and version["version_seq"] <= target["version_seq"],
                    "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    for audit in domain["inclusion_audits"]:
        validate_inclusion(audit, envelopes)
    statements = {s["statement_id"]: s for s in domain["speaker_statements"]}
    for statement in domain["speaker_statements"]:
        validate_statement(statement)
    for ruling in domain["materiality_rulings"]:
        validate_ruling(ruling, statements, envelopes)
    referenced = set()
    for audit in domain["inclusion_audits"]:
        referenced.add(audit["created_envelope_id"])
        referenced.update(e["envelope_id"] for e in audit["escalations"] + audit["disclosures"])
    referenced.update(r["envelope_id"] for r in domain["materiality_rulings"])
    require(referenced == set(envelope_ids), "ERR_INVALID_EFFECT_HISTORY",
            "/effectiveness_envelopes")
    return object_index(domain)


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
            or (mandate["tenant_node_id"], mandate["case_id"]) != (ctx["tenant_node_id"], ctx["case_id"])
            or not {cmd["target_object_id"], ctx["case_id"]} & set(mandate["allowed_target_ids"])):
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
            and ctx["new_envelope_id"] is not None, "ERR_INCOMPLETE_FOUR_TUPLE", "/server_context")
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


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


def envelope_input(data):
    return data["command"]["payload"]["effectiveness_envelope_input"]


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
    expected_kind = {RECORD_AUDIT: "CASE", RECORD_STATEMENT: "CASE", ESCALATE: "AUDIT",
                     PROCEED: "AUDIT", CONFIRM: "STATEMENT", WITHDRAW: "STATEMENT",
                     RULE: "STATEMENT"}[cmd["requested_action"]]
    kind = target.get("kind") or ("AUDIT" if "audit_id" in target else
                                  "STATEMENT" if "statement_id" in target else
                                  "MATERIALITY_RULING" if "ruling_id" in target else None)
    if expected_kind == "CASE":
        kind = "CASE" if target.get("object_id") == ctx["case_id"] and target.get("kind") == "CASE" else kind
    require(kind == expected_kind, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    require(cmd["target_expected_version_seq"] == target["version_seq"],
            "ERR_VERSION_SEQ_MISMATCH", "/command/target_expected_version_seq")
    deps = [{"object_id": case["object_id"], "version_seq": case["version_seq"]}]
    require(cmd["dependency_versions"] == deps, "ERR_DEPENDENCY_VERSION_MISMATCH",
            "/command/dependency_versions")
    record, attempt = before["command_record"], before["command_attempt"]
    expected = {"command_id": cmd["command_id"], "tenant_node_id": ctx["tenant_node_id"],
                "principal_id": ctx["authenticated_principal_id"],
                "action_id": cmd["requested_action"], "idempotency_key": cmd["idempotency_key"],
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
    refs += [disclosure["named_review_ref"]["evidence_ref"]
             for audit in before["domain_objects"]["inclusion_audits"]
             for disclosure in audit["disclosures"]]
    if POWERS[cmd["requested_action"]] is not None:
        inp = envelope_input(data)
        refs += inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    if cmd["requested_action"] == WITHDRAW:
        refs.append(cmd["payload"]["policy_ref"])
    if cmd["requested_action"] == PROCEED:
        if ctx["program_gap_authorization"] is not None:
            refs.append(ctx["program_gap_authorization"]["scope_ref"])
        if ctx["named_review"] is not None:
            refs.append(ctx["named_review"]["evidence_ref"])
    require(all(evidence_ref_resolved(ctx, ref) for ref in refs),
            "ERR_UNRESOLVED_EVIDENCE_REFERENCE", "/server_context/evidence_snapshots")
    return objects, target, case


# ---------------------------------------------------------------- diagnostics
def decision_diagnostics(data, objects, target, status):
    cmd, ctx = data["command"], data["server_context"]
    action = cmd["requested_action"]
    payload = cmd["payload"]
    try:
        require(member_mapping(ctx, ctx["authenticated_principal_id"]) is not None
                and member_mapping(ctx, ctx["authenticated_principal_id"]) == ctx["authenticated_member_id"],
                "ERR_SOD_IDENTITY_UNMAPPED", "/server_context/authenticated_member_id")
        if POWERS[action] is not None:
            require(status == "VERIFIED_ACTIVE",
                    STATUS_CODES.get(status, "ERR_UNAUTHORIZED_POWER"),
                    "/server_context/generated_auth_receipt")
            claim = cmd["mandate_claim"]
            require(claim["claimed_power"] == POWERS[action], "ERR_UNAUTHORIZED_POWER",
                    "/command/mandate_claim")
            require(ctx["new_envelope_id"] not in objects
                    and ctx["new_envelope_id"] not in
                    {e["envelope_id"] for e in data["before"]["domain_objects"]["effectiveness_envelopes"]},
                    "ERR_NEW_OBJECT_ID_CONFLICT", "/server_context/new_envelope_id")
            inp = envelope_input(data)
            current = next(m for m in ctx["authority_snapshots"]
                           if m["mandate_id"] == claim["mandate_id"])
            scope = inp["scope_spec"]
            require(inp["mandate_clause_ref"] == current["clause_reference"]
                    and (scope["tenant_node_id"], scope["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"])
                    and scope["effective_from"] <= ctx["evaluated_at"] < scope["effective_until"]
                    <= current["valid_until"], "ERR_EFFECTIVENESS_SCOPE_MISMATCH",
                    "/command/payload/effectiveness_envelope_input")
        if action == RECORD_AUDIT:
            _diagnose_audit(data, objects)
        elif action == ESCALATE:
            _diagnose_escalate(data, objects, target)
        elif action == PROCEED:
            _diagnose_proceed(data, objects, target)
        elif action == RECORD_STATEMENT:
            _diagnose_statement(data, objects)
        elif action == CONFIRM:
            _diagnose_confirm(data, target)
        elif action == WITHDRAW:
            _diagnose_withdraw(data, target)
        else:
            _diagnose_ruling(data, target)
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


def _diagnose_audit(data, objects):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(payload["audit_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/audit_id")
    window = payload["participation_window"]
    require(window["start"] < window["end"], "ERR_INCLUSION_WINDOW_INVALID",
            "/command/payload/participation_window")
    require(events_consistent(payload["events"]), "ERR_INCLUSION_LEDGER_INCONSISTENT",
            "/command/payload/events")
    for index, stratum in enumerate(payload["strata"]):
        require(events_consistent(stratum["events"]), "ERR_INCLUSION_LEDGER_INCONSISTENT",
                "/command/payload/strata/" + str(index))
    population = payload["eligible_population"]
    if population["kind"] == "INTERVAL":
        require(population["lower_bound"] < population["upper_bound"],
                "ERR_INCLUSION_LEDGER_INCONSISTENT", "/command/payload/eligible_population")
    status, _, _ = derive_inclusion_status(window, population, payload["floor"],
                                           payload["events"], payload["strata"],
                                           ctx["evaluated_at"])
    if status in {"BELOW_REQUIRED_FLOOR", "INDETERMINATE_INSUFFICIENT_DATA"}:
        require(isinstance(payload["review_deadline_at"], str)
                and payload["review_deadline_at"] > ctx["evaluated_at"]
                and b.known_member(payload["review_owner_member_id"]),
                "ERR_INCLUSION_REVIEW_NOT_ASSIGNED", "/command/payload/review_deadline_at")
    else:
        require(payload["review_deadline_at"] is None
                and payload["review_owner_member_id"] is None,
                "ERR_INCLUSION_REVIEW_NOT_ASSIGNED", "/command/payload/review_deadline_at")


def _diagnose_escalate(data, objects, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(target["audit_status"] in {"BELOW_REQUIRED_FLOOR",
                                        "INDETERMINATE_INSUFFICIENT_DATA"},
            "ERR_INCLUSION_GAP_NOT_ESCALATABLE", "/audit_status")
    require(all(payload["escalation_id"] != row["escalation_id"]
                for row in target["escalations"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/escalation_id")
    window = payload["remediation_window"]
    require(window["start"] < window["end"] <= payload["review_deadline_at"]
            and payload["review_deadline_at"] > ctx["evaluated_at"],
            "ERR_INCLUSION_REMEDIATION_WINDOW_INVALID",
            "/command/payload/remediation_window")
    require(b.known_member(payload["handler_member_id"]), "ERR_SOD_IDENTITY_UNMAPPED",
            "/command/payload/handler_member_id")


def _diagnose_proceed(data, objects, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(target["audit_status"] in {"BELOW_REQUIRED_FLOOR",
                                        "INDETERMINATE_INSUFFICIENT_DATA"},
            "ERR_INCLUSION_GAP_NOT_ESCALATABLE", "/audit_status")
    gaps = set(payload["gap_codes"])
    require(gaps <= NON_CORE_GAP_CODES, "ERR_INCLUSION_CORE_GAP_NOT_WAIVABLE",
            "/command/payload/gap_codes")
    require(ctx["evaluated_at"] >= target["review_deadline_at"],
            "ERR_INCLUSION_DEADLINE_NOT_REACHED", "/server_context/evaluated_at")
    authorization = ctx["program_gap_authorization"]
    require(authorization is not None, "ERR_INCLUSION_GAP_AUTHORIZATION_MISSING",
            "/server_context/program_gap_authorization")
    require(authorization["tenant_node_id"] == ctx["tenant_node_id"]
            and authorization["case_id"] == ctx["case_id"],
            "ERR_INCLUSION_GAP_AUTHORIZATION_SCOPE_MISMATCH",
            "/server_context/program_gap_authorization")
    require(authorization["valid_from"] <= ctx["evaluated_at"] < authorization["valid_until"],
            "ERR_INCLUSION_GAP_AUTHORIZATION_EXPIRED",
            "/server_context/program_gap_authorization")
    require(gaps <= set(authorization["allowed_non_core_gap_codes"]),
            "ERR_INCLUSION_GAP_AUTHORIZATION_SCOPE_MISMATCH",
            "/server_context/program_gap_authorization/allowed_non_core_gap_codes")
    review = ctx["named_review"]
    require(review is not None, "ERR_INCLUSION_NAMED_REVIEW_MISSING",
            "/server_context/named_review")
    require(review["reviewer_member_id"] == member_mapping(ctx, review["reviewer_principal_id"]),
            "ERR_SOD_IDENTITY_UNMAPPED", "/server_context/named_review")
    require(review["decision"] == "APPROVED", "ERR_INCLUSION_NAMED_REVIEW_REJECTED",
            "/server_context/named_review")
    require(review["reviewed_at"] <= ctx["evaluated_at"], "ERR_INCLUSION_NAMED_REVIEW_MISSING",
            "/server_context/named_review")
    reviewer = next((m for m in ctx["authority_snapshots"]
                     if m["principal_id"] == review["reviewer_principal_id"]
                     and "REVIEW_PROCEDURAL_GAP" in m["powers"]), None)
    require(reviewer is not None and reviewer["status"] == "ACTIVE"
            and reviewer["valid_from"] <= review["reviewed_at"] < reviewer["valid_until"]
            and (reviewer["tenant_node_id"], reviewer["case_id"]) == (ctx["tenant_node_id"], ctx["case_id"]),
            "ERR_INCLUSION_NAMED_REVIEW_MISSING", "/server_context/named_review")
    require(all(payload["disclosure_id"] != row["disclosure_id"]
                for row in target["disclosures"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/disclosure_id")


def _diagnose_statement(data, objects):
    payload = data["command"]["payload"]
    require(payload["statement_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/statement_id")
    translation = payload["translation"]
    if translation is not None:
        require(translation["translation_id"] != payload["statement_id"],
                "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/translation/translation_id")


def _diagnose_confirm(data, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(ctx["authenticated_member_id"] == target["speaker_member_id"],
            "ERR_CONFIRMATION_SUBJECT_MISMATCH", "/server_context/authenticated_member_id")
    require(all(c["confirmation_id"] != payload["confirmation_id"]
                for c in target["expression_confirmations"]),
            "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/confirmation_id")
    ref = payload["text_ref"]
    if ref["text_kind"] == "ORIGINAL":
        require(ref["translation_id"] is None, "ERR_CONFIRMATION_TEXT_NOT_FOUND",
                "/command/payload/text_ref")
        require(1 <= ref["text_version_seq"] <= len(target["original_versions"]),
                "ERR_CONFIRMATION_TEXT_NOT_FOUND", "/command/payload/text_ref")
        expected = text_digest(target["original_versions"][ref["text_version_seq"] - 1]["text"])
    else:
        translation = next((t for t in target["translations"]
                            if t["translation_id"] == ref["translation_id"]), None)
        require(translation is not None, "ERR_CONFIRMATION_TEXT_NOT_FOUND",
                "/command/payload/text_ref")
        require(1 <= ref["text_version_seq"] <= len(translation["versions"]),
                "ERR_CONFIRMATION_TEXT_NOT_FOUND", "/command/payload/text_ref")
        expected = text_digest(translation["versions"][ref["text_version_seq"] - 1]["text"])
    require(ref["text_sha256"] == expected, "ERR_CONFIRMATION_TEXT_MISMATCH",
            "/command/payload/text_ref/text_sha256")


def _diagnose_withdraw(data, target):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(ctx["authenticated_member_id"] == target["speaker_member_id"],
            "ERR_STATEMENT_SUBJECT_MISMATCH", "/server_context/authenticated_member_id")
    if payload["target_kind"] == "ORIGINAL":
        require(payload["translation_id"] is None, "ERR_DISPLAY_PERMISSION_NOT_FOUND",
                "/command/payload")
        exists = True
        taken = any(w["target_kind"] == "ORIGINAL" for w in target["display_withdrawals"])
    else:
        exists = any(t["translation_id"] == payload["translation_id"]
                     for t in target["translations"])
        taken = any(w["target_kind"] == "TRANSLATION"
                    and w["translation_id"] == payload["translation_id"]
                    for w in target["display_withdrawals"])
    require(exists, "ERR_DISPLAY_PERMISSION_NOT_FOUND", "/command/payload")
    require(not taken, "ERR_DISPLAY_PERMISSION_ALREADY_WITHDRAWN", "/command/payload")
    require(all(w["withdrawal_id"] != payload["withdrawal_id"]
                for w in target["display_withdrawals"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/withdrawal_id")


def _diagnose_ruling(data, target):
    payload = data["command"]["payload"]
    require(payload["statement_version_seq"] == target["version_seq"],
            "ERR_MATERIALITY_RULING_SUBJECT_MISMATCH",
            "/command/payload/statement_version_seq")


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


def statement_object(data):
    cmd, ctx = data["command"], data["server_context"]
    payload = cmd["payload"]
    now = ctx["evaluated_at"]
    original = {"version_seq": 1, "language": payload["original"]["language"],
                "text": payload["original"]["text"],
                "display_permission": payload["original"]["display_permission"],
                "recorded_at": now, "recorded_command_id": cmd["command_id"]}
    translations = []
    translation = payload["translation"]
    if translation is not None:
        translations.append({
            "translation_id": translation["translation_id"],
            "language": translation["language"],
            "current_version_seq": 1,
            "versions": [{"version_seq": 1, "language": translation["language"],
                          "text": translation["text"],
                          "display_permission": translation["display_permission"],
                          "source_original_version_seq": 1,
                          "source_text_sha256": text_digest(payload["original"]["text"]),
                          "recorded_at": now,
                          "recorded_command_id": cmd["command_id"]}],
        })
    statement = {"statement_id": payload["statement_id"],
                 "tenant_node_id": ctx["tenant_node_id"], "case_id": ctx["case_id"],
                 "version_seq": 1, "speaker_member_id": ctx["authenticated_member_id"],
                 "original_versions": [original], "current_original_version_seq": 1,
                 "translations": translations, "expression_confirmations": [],
                 "display_withdrawals": [], "frozen_at": None,
                 "created_command_id": cmd["command_id"]}
    statement["display_state"] = derive_display_state(statement)
    statement["representation"] = derive_representation(statement)
    return statement


def expected_applied_domain(data):
    cmd, ctx, payload = data["command"], data["server_context"], data["command"]["payload"]
    action = cmd["requested_action"]
    domain = copy.deepcopy(data["before"]["domain_objects"])
    if action == RECORD_AUDIT:
        status, basis, _ = derive_inclusion_status(payload["participation_window"],
                                                   payload["eligible_population"],
                                                   payload["floor"], payload["events"],
                                                   payload["strata"], ctx["evaluated_at"])
        audit = {"audit_id": payload["audit_id"], "tenant_node_id": ctx["tenant_node_id"],
                 "case_id": ctx["case_id"], "version_seq": 1,
                 "participation_window": copy.deepcopy(payload["participation_window"]),
                 "eligible_population": copy.deepcopy(payload["eligible_population"]),
                 "floor": copy.deepcopy(payload["floor"]),
                 "strata": copy.deepcopy(payload["strata"]),
                 "events": copy.deepcopy(payload["events"]),
                 "support_measures": copy.deepcopy(payload["support_measures"]),
                 "limitations": copy.deepcopy(payload["limitations"]),
                 "audit_status": status, "status_basis": basis,
                 "gate_disposition": derive_gate_disposition(status, []),
                 "review_deadline_at": payload["review_deadline_at"],
                 "review_owner_member_id": payload["review_owner_member_id"],
                 "escalations": [], "disclosures": [],
                 "evaluated_at": ctx["evaluated_at"],
                 "created_command_id": cmd["command_id"],
                 "created_envelope_id": ctx["new_envelope_id"]}
        domain["inclusion_audits"].append(audit)
        versions = [{"object_id": audit["audit_id"], "version_seq": 1}]
        domain["effectiveness_envelopes"].append(envelope(data, action, versions))
    elif action == ESCALATE:
        audit = next(a for a in domain["inclusion_audits"]
                     if a["audit_id"] == cmd["target_object_id"])
        audit["escalations"].append({
            "escalation_id": payload["escalation_id"],
            "escalation_kind": payload["escalation_kind"],
            "remediation_window": copy.deepcopy(payload["remediation_window"]),
            "handler_member_id": payload["handler_member_id"],
            "review_deadline_at": payload["review_deadline_at"],
            "recorded_at": ctx["evaluated_at"],
            "envelope_id": ctx["new_envelope_id"]})
        audit["version_seq"] += 1
        versions = [{"object_id": audit["audit_id"], "version_seq": audit["version_seq"]}]
        domain["effectiveness_envelopes"].append(envelope(data, action, versions))
    elif action == PROCEED:
        audit = next(a for a in domain["inclusion_audits"]
                     if a["audit_id"] == cmd["target_object_id"])
        authorization, review = ctx["program_gap_authorization"], ctx["named_review"]
        audit["disclosures"].append({
            "disclosure_id": payload["disclosure_id"],
            "gap_codes": copy.deepcopy(payload["gap_codes"]),
            "limitation_text": payload["limitation_text"],
            "preauthorization_ref": {"authorization_id": authorization["authorization_id"],
                                     "version_seq": authorization["version_seq"]},
            "named_review_ref": {"review_id": review["review_id"],
                                 "reviewer_principal_id": review["reviewer_principal_id"],
                                 "reviewer_member_id": review["reviewer_member_id"],
                                 "reviewed_at": review["reviewed_at"],
                                 "evidence_ref": copy.deepcopy(review["evidence_ref"])},
            "recorded_at": ctx["evaluated_at"],
            "envelope_id": ctx["new_envelope_id"]})
        audit["version_seq"] += 1
        audit["gate_disposition"] = derive_gate_disposition(audit["audit_status"],
                                                           audit["disclosures"])
        versions = [{"object_id": audit["audit_id"], "version_seq": audit["version_seq"]}]
        domain["effectiveness_envelopes"].append(envelope(data, action, versions))
    elif action == RECORD_STATEMENT:
        statement = statement_object(data)
        domain["speaker_statements"].append(statement)
        versions = [{"object_id": statement["statement_id"], "version_seq": 1}]
    elif action == CONFIRM:
        statement = next(s for s in domain["speaker_statements"]
                         if s["statement_id"] == cmd["target_object_id"])
        ref = payload["text_ref"]
        statement["expression_confirmations"].append({
            "confirmation_id": payload["confirmation_id"], "text_kind": ref["text_kind"],
            "translation_id": ref["translation_id"],
            "text_version_seq": ref["text_version_seq"],
            "text_sha256": ref["text_sha256"],
            "confirmed_by_member_id": ctx["authenticated_member_id"],
            "confirmed_at": ctx["evaluated_at"],
            "recorded_command_id": cmd["command_id"]})
        statement["version_seq"] += 1
        statement["display_state"] = derive_display_state(statement)
        statement["representation"] = derive_representation(statement)
        versions = [{"object_id": statement["statement_id"],
                     "version_seq": statement["version_seq"]}]
    elif action == WITHDRAW:
        statement = next(s for s in domain["speaker_statements"]
                         if s["statement_id"] == cmd["target_object_id"])
        statement["display_withdrawals"].append({
            "withdrawal_id": payload["withdrawal_id"],
            "target_kind": payload["target_kind"],
            "translation_id": payload["translation_id"],
            "policy_ref": copy.deepcopy(payload["policy_ref"]),
            "reason": payload["reason"], "withdrawn_at": ctx["evaluated_at"],
            "recorded_command_id": cmd["command_id"]})
        statement["version_seq"] += 1
        statement["display_state"] = derive_display_state(statement)
        statement["representation"] = derive_representation(statement)
        versions = [{"object_id": statement["statement_id"],
                     "version_seq": statement["version_seq"]}]
    else:
        statement = next(s for s in domain["speaker_statements"]
                         if s["statement_id"] == cmd["target_object_id"])
        ruling = {"ruling_id": payload["ruling_id"], "statement_id": statement["statement_id"],
                  "statement_version_seq": payload["statement_version_seq"], "version_seq": 1,
                  "ballot_round_ref": payload["ballot_round_ref"],
                  "ruling": payload["ruling"], "rationale": payload["rationale"],
                  "recorded_at": ctx["evaluated_at"],
                  "envelope_id": ctx["new_envelope_id"],
                  "actor_member_id": ctx["authenticated_member_id"],
                  "automatic_ballot_pause": False}
        domain["materiality_rulings"].append(ruling)
        versions = [{"object_id": ruling["ruling_id"], "version_seq": 1}]
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
    require(result is not None, "ERR_COMMAND_RESULT_MISMATCH", "/after/execution_outcome/command_result")
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
