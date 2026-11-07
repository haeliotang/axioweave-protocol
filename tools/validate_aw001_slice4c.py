"""Slice 4C offline synthetic output withdrawal, recovery and deployment exchange.

Validation only: never moves files, never restores data, never deploys anything,
and never starts the application.
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
SPEC = ROOT / "specs/aw001_slice4/4c"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

STAGE, PUBLISH, WITHDRAW, DISPATCH, RECOVERY, TEMPLATE, DEPLOY = (
    "STAGE_OUTPUT_RELEASE", "PUBLISH_LOCAL_OUTPUT", "WITHDRAW_OUTPUT",
    "DISPATCH_NOTIFICATION", "RECORD_RECOVERY_MANIFEST", "INSTALL_TEMPLATE_PACKAGE",
    "RECORD_DEPLOYMENT_MANIFEST")
ACTIONS = (STAGE, PUBLISH, WITHDRAW, DISPATCH, RECOVERY, TEMPLATE, DEPLOY)
POWERS = {action: action for action in ACTIONS}
PAYLOADS = {STAGE: "Payload0", PUBLISH: "Payload1", WITHDRAW: "Payload2",
            DISPATCH: "Payload3", RECOVERY: "Payload4", TEMPLATE: "Payload5",
            DEPLOY: "Payload6"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_OUTPUT_RELEASE",
          "CHECK_RECOVERY_TEMPLATE_DEPLOYMENT", "CHECK_ALLOWED_WRITE_SET",
          "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {STAGE: "CREATE_OUTPUT_STAGING", PUBLISH: "PUBLISH_STAGING_LOCAL",
          WITHDRAW: "WITHDRAW_ARTIFACT_AND_BUMP_POLICY",
          DISPATCH: "DISPATCH_NOTIFICATION_ONCE",
          RECOVERY: "CREATE_RECOVERY_MANIFEST",
          TEMPLATE: "CREATE_TEMPLATE_INSTALLATION",
          DEPLOY: "CREATE_DEPLOYMENT_RECORD"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
CURRENT_CONTRACT = "0.5.0-rc1-slice4c"
DEPLOYMENT_REFS = ("build_ref", "schema_ref", "action_registry_ref", "spec_frozen_ref",
                   "test_report_ref", "migration_plan_ref", "backup_recovery_ref",
                   "approval_ref", "rollback_build_ref")


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
    require(value["contract_version"] == CURRENT_CONTRACT,
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
def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"],
                                  "kind": "PROTECTIVE_ORDER"}
    for row in domain["output_artifacts"]:
        index[row["artifact_id"]] = row
    for row in domain["output_stagings"]:
        index[row["staging_id"]] = row
    for row in domain["output_withdrawals"]:
        index[row["withdrawal_id"]] = row
    for row in domain["notification_tasks"]:
        index[row["notification_id"]] = row
    for row in domain["recovery_manifests"]:
        index[row["manifest_id"]] = row
    for row in domain["template_installations"]:
        index[row["installation_id"]] = row
    for row in domain["deployment_records"]:
        index[row["deployment_id"]] = row
    return index


def object_case(domain, object_id):
    row = object_index(domain).get(object_id)
    return row.get("case_id") if row else None


def case_policy(domain, case_id):
    return next((row for row in domain["case_access_policies"]
                 if row["case_id"] == case_id), None)


def policy_version(domain, case_id):
    row = case_policy(domain, case_id)
    return row["access_policy_version"] if row else 0


def artifact_by_id(domain, artifact_id):
    return next((row for row in domain["output_artifacts"]
                 if row["artifact_id"] == artifact_id), None)


def staging_by_id(domain, staging_id):
    return next((row for row in domain["output_stagings"]
                 if row["staging_id"] == staging_id), None)


def task_by_id(domain, notification_id):
    return next((row for row in domain["notification_tasks"]
                 if row["notification_id"] == notification_id), None)


def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


def context_digest(ctx):
    return digest({k: v for k, v in ctx.items() if k != "generated_auth_receipt"})


# ---------------------------------------------------------------- domain
def derived_quarantine(manifest):
    if manifest["digest_status"] == "MISMATCHED":
        return True
    return any(record["material_status"] == "CORRUPT"
               or record["digest_status"] == "MISMATCHED"
               for record in manifest["records"])


def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    objects = object_index(domain)
    ids = [row[key] for name, key in (
        ("output_artifacts", "artifact_id"), ("output_stagings", "staging_id"),
        ("output_withdrawals", "withdrawal_id"),
        ("notification_tasks", "notification_id"),
        ("recovery_manifests", "manifest_id"),
        ("template_installations", "installation_id"),
        ("deployment_records", "deployment_id")) for row in domain[name]]
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
    policies = {row["case_id"]: row for row in domain["case_access_policies"]}
    require(len(policies) == len(domain["case_access_policies"]), "ERR_DUPLICATE_OBJECT_ID",
            "/case_access_policies")
    withdrawals_by_artifact = {}
    for row in domain["output_withdrawals"]:
        withdrawals_by_artifact.setdefault(row["artifact_id"], []).append(row)
    for case_id in {row["case_id"] for row in domain["output_artifacts"]}:
        policy = policies.get(case_id)
        expected = 1 + len([row for row in domain["output_withdrawals"]
                            if row["case_id"] == case_id])
        require(policy is not None and policy["access_policy_version"] == expected,
                "ERR_CASE_POLICY_VERSION_MISMATCH", "/case_access_policies")
    for row in domain["output_artifacts"]:
        require(row["case_id"] in policies, "ERR_CASE_POLICY_VERSION_MISMATCH",
                "/output_artifacts")
        existing = withdrawals_by_artifact.get(row["artifact_id"], [])
        if existing:
            require(len(existing) == 1 and row["display_status"] == "WITHDRAWN"
                    and row["current_policy_version"]
                    == existing[0]["policy_version_seq"],
                    "ERR_OUTPUT_WITHDRAWN", "/output_artifacts")
        else:
            require(row["display_status"] == "ACTIVE"
                    and row["current_policy_version"]
                    <= policy_version(domain, row["case_id"]),
                    "ERR_OUTPUT_WITHDRAWN", "/output_artifacts")
    for row in domain["output_stagings"]:
        artifact = artifact_by_id(domain, row["artifact_id"])
        require(artifact is not None and artifact["case_id"] == row["case_id"],
                "ERR_OUTPUT_STALE_POLICY", "/output_stagings")
        if row["status"] == "STAGED":
            require(row["published_file_ref"] is None and row["released_at"] is None,
                    "ERR_OUTPUT_STALE_POLICY", "/output_stagings")
        else:
            require(row["published_file_ref"] is not None
                    and row["released_at"] is not None,
                    "ERR_OUTPUT_STALE_POLICY", "/output_stagings")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == STAGE
                and {"object_id": row["staging_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/output_stagings")
        if row["status"] == "PUBLISHED":
            published = envelopes.get(row["published_envelope_id"])
            require(published is not None and published["action_type"] == PUBLISH
                    and published["target_object_id"] == row["staging_id"]
                    and {"object_id": row["staging_id"], "version_seq": 1}
                    in published["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                    "/output_stagings")
        else:
            require(row["published_envelope_id"] is None, "ERR_INVALID_EFFECT_HISTORY",
                    "/output_stagings")
    for row in domain["output_withdrawals"]:
        require(artifact_by_id(domain, row["artifact_id"]) is not None,
                "ERR_OUTPUT_WITHDRAWN", "/output_withdrawals")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == WITHDRAW
                and creation["target_object_id"] == row["artifact_id"]
                and {"object_id": row["withdrawal_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/output_withdrawals")
    for row in domain["notification_tasks"]:
        require(artifact_by_id(domain, row["artifact_id"]) is not None,
                "ERR_NOTIFICATION_STALE_PERMISSION", "/notification_tasks")
        if row["status"] == "DISPATCHED":
            require(row["dispatched_at"] is not None and row["attempts"] == 1,
                    "ERR_NOTIFICATION_ALREADY_DISPATCHED", "/notification_tasks")
            creation = envelopes.get(row["created_envelope_id"])
            require(creation is not None and creation["action_type"] == DISPATCH
                    and {"object_id": row["notification_id"], "version_seq": 1}
                    in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                    "/notification_tasks")
        else:
            require(row["dispatched_at"] is None and row["attempts"] == 0,
                    "ERR_NOTIFICATION_ALREADY_DISPATCHED", "/notification_tasks")
    for row in domain["recovery_manifests"]:
        require(row["quarantine_required"] == derived_quarantine(row),
                "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/recovery_manifests")
        if row["quarantine_required"]:
            require(all(record["read_only"] for record in row["records"])
                    and row["procedural_effect"] in {"REQUIRES_REAUTHORIZATION", "UNKNOWN"},
                    "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/recovery_manifests")
        else:
            require(all(record["read_only"] for record in row["records"])
                    and row["procedural_effect"] == "PRESERVED_READ_ONLY",
                    "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/recovery_manifests")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == RECOVERY
                and {"object_id": row["manifest_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/recovery_manifests")
    for row in domain["template_installations"]:
        require(row["mode"] == "SIMULATION"
                and CURRENT_CONTRACT in row["supported_contract_versions"],
                "ERR_TEMPLATE_CONTRACT_UNSUPPORTED", "/template_installations")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == TEMPLATE
                and {"object_id": row["installation_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/template_installations")
    for row in domain["deployment_records"]:
        require(row["actions_enabled"] == (row["compatibility_status"] == "COMPATIBLE"),
                "ERR_DEPLOYMENT_REFS_MISSING", "/deployment_records")
        creation = envelopes.get(row["created_envelope_id"])
        require(creation is not None and creation["action_type"] == DEPLOY
                and {"object_id": row["deployment_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/deployment_records")
    referenced = set()
    for row in domain["output_stagings"]:
        referenced.add(row["created_envelope_id"])
        if row["published_envelope_id"] is not None:
            referenced.add(row["published_envelope_id"])
    referenced.update(row["created_envelope_id"] for row in domain["output_withdrawals"])
    referenced.update(row["created_envelope_id"] for row in domain["notification_tasks"]
                      if row["created_envelope_id"] is not None)
    referenced.update(row["created_envelope_id"] for row in domain["recovery_manifests"])
    referenced.update(row["created_envelope_id"] for row in domain["template_installations"])
    referenced.update(row["created_envelope_id"] for row in domain["deployment_records"])
    for envelope in domain["effectiveness_envelopes"]:
        require(envelope["action_type"] in ACTIONS
                and envelope["effect_parameters_sha256"] == digest(envelope["payload_snapshot"]),
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    require(referenced == set(envelopes), "ERR_INVALID_EFFECT_HISTORY",
            "/effectiveness_envelopes")
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
    target = objects.get(cmd["target_object_id"])
    require(target is not None, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
    expected_kind = {STAGE: "ARTIFACT", WITHDRAW: "ARTIFACT", PUBLISH: "STAGING",
                     DISPATCH: "NOTIFICATION", RECOVERY: "CASE", TEMPLATE: "CASE",
                     DEPLOY: "CASE"}[cmd["requested_action"]]
    kind = ("CASE" if target.get("kind") == "CASE"
            else "STAGING" if "staging_id" in target
            else "NOTIFICATION" if "notification_id" in target
            else "ARTIFACT" if "artifact_id" in target else "OTHER")
    require(kind == expected_kind, "ERR_INVALID_ACTION_TARGET", "/command/target_object_id")
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
    payload = cmd["payload"]
    inp = payload["effectiveness_envelope_input"]
    refs = [member["source_ref"] for member in ctx["member_directory"]]
    refs += inp["evidence_refs"] + [inp["remedy_spec"]["rollback_or_compensation_procedure_ref"]]
    if cmd["requested_action"] == STAGE:
        refs.append(payload["staged_file_ref"])
    elif cmd["requested_action"] == PUBLISH:
        refs.append(payload["published_file_ref"])
    elif cmd["requested_action"] == TEMPLATE:
        refs += [payload["local_adoption_ref"], payload["source_ref"]]
    elif cmd["requested_action"] == DEPLOY:
        refs += [payload[key] for key in DEPLOYMENT_REFS]
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
        if action == STAGE:
            _diagnose_stage(data, ctx, target)
        elif action == PUBLISH:
            _diagnose_publish(data, ctx, target)
        elif action == WITHDRAW:
            _diagnose_withdraw(data, ctx, target)
        elif action == DISPATCH:
            _diagnose_dispatch(data, ctx, target)
        elif action == RECOVERY:
            _diagnose_recovery(data, objects)
        elif action == TEMPLATE:
            _diagnose_template(data, objects)
        else:
            _diagnose_deploy(data, objects)
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


def _diagnose_stage(data, ctx, artifact):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(artifact["display_status"] == "ACTIVE", "ERR_OUTPUT_WITHDRAWN",
            "/domain_objects/output_artifacts")
    require(payload["rendered_policy_version"] == policy_version(domain, ctx["case_id"]),
            "ERR_OUTPUT_STALE_POLICY", "/command/payload/rendered_policy_version")
    require(staging_by_id(domain, payload["staging_id"]) is None,
            "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/staging_id")


def _diagnose_publish(data, ctx, staging):
    domain = data["before"]["domain_objects"]
    require(staging["status"] == "STAGED", "ERR_OUTPUT_STALE_POLICY",
            "/domain_objects/output_stagings")
    artifact = artifact_by_id(domain, staging["artifact_id"])
    require(artifact is not None and artifact["display_status"] == "ACTIVE",
            "ERR_OUTPUT_WITHDRAWN", "/domain_objects/output_artifacts")
    require(staging["rendered_policy_version"] == policy_version(domain, ctx["case_id"]),
            "ERR_OUTPUT_STALE_POLICY", "/domain_objects/output_stagings")


def _diagnose_withdraw(data, ctx, artifact):
    payload, domain = data["command"]["payload"], data["before"]["domain_objects"]
    require(artifact["display_status"] == "ACTIVE", "ERR_OUTPUT_WITHDRAWN",
            "/domain_objects/output_artifacts")
    require(all(row["withdrawal_id"] != payload["withdrawal_id"]
                for row in domain["output_withdrawals"]), "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/withdrawal_id")


def _diagnose_dispatch(data, ctx, task):
    domain = data["before"]["domain_objects"]
    require(task["status"] == "PENDING", "ERR_NOTIFICATION_ALREADY_DISPATCHED",
            "/domain_objects/notification_tasks")
    artifact = artifact_by_id(domain, task["artifact_id"])
    require(artifact is not None and artifact["display_status"] == "ACTIVE",
            "ERR_NOTIFICATION_STALE_PERMISSION", "/domain_objects/notification_tasks")
    require(task["recorded_policy_version"] == policy_version(domain, ctx["case_id"]),
            "ERR_NOTIFICATION_STALE_PERMISSION", "/domain_objects/notification_tasks")


def _diagnose_recovery(data, objects):
    payload = data["command"]["payload"]
    require(payload["manifest_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/manifest_id")
    manifest = {**payload, "digest_status": payload["digest_status"]}
    quarantine = derived_quarantine(manifest)
    if quarantine:
        require(payload["procedural_effect"] in {"REQUIRES_REAUTHORIZATION", "UNKNOWN"},
                "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/command/payload/procedural_effect")
    else:
        require(payload["procedural_effect"] == "PRESERVED_READ_ONLY",
                "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/command/payload/procedural_effect")
    require(all(record["read_only"] for record in payload["records"]),
            "ERR_RECOVERY_MANIFEST_INCONSISTENT", "/command/payload/records")


def _diagnose_template(data, objects):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(payload["installation_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/installation_id")
    require(CURRENT_CONTRACT in payload["supported_contract_versions"],
            "ERR_TEMPLATE_CONTRACT_UNSUPPORTED",
            "/command/payload/supported_contract_versions")


def _diagnose_deploy(data, objects):
    payload, ctx = data["command"]["payload"], data["server_context"]
    require(payload["deployment_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
            "/command/payload/deployment_id")
    require(any(row["member_id"] == payload["operator_member_id"] for row in ctx["member_directory"]),
            "ERR_SOD_IDENTITY_UNMAPPED", "/command/payload/operator_member_id")


# ---------------------------------------------------------------- expected state
def envelope(data, action, resulting_versions, target_id=None):
    cmd, ctx, payload = data["command"], data["server_context"], data["command"]["payload"]
    inp = payload["effectiveness_envelope_input"]
    claim = cmd["mandate_claim"]
    return {"envelope_id": ctx["new_envelope_id"], "action_type": action,
            "target_object_id": target_id or cmd["target_object_id"],
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
    if action == STAGE:
        row = {"staging_id": payload["staging_id"], "tenant_node_id": ctx["tenant_node_id"],
               "case_id": case_id, "artifact_id": cmd["target_object_id"],
               "staged_file_ref": copy.deepcopy(payload["staged_file_ref"]),
               "rendered_policy_version": payload["rendered_policy_version"],
               "status": "STAGED", "published_file_ref": None, "released_at": None,
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"],
               "published_envelope_id": None}
        domain["output_stagings"].append(row)
        versions = [{"object_id": row["staging_id"], "version_seq": 1}]
    elif action == PUBLISH:
        staging = staging_by_id(domain, cmd["target_object_id"])
        staging.update(status="PUBLISHED",
                       published_file_ref=copy.deepcopy(payload["published_file_ref"]),
                       released_at=ctx["evaluated_at"],
                       published_envelope_id=ctx["new_envelope_id"])
        versions = [{"object_id": staging["staging_id"], "version_seq": 1}]
    elif action == WITHDRAW:
        artifact = artifact_by_id(domain, cmd["target_object_id"])
        policy = case_policy(domain, case_id)
        policy["access_policy_version"] += 1
        policy["updated_by_command_id"] = cmd["command_id"]
        artifact["display_status"] = "WITHDRAWN"
        artifact["current_policy_version"] = policy["access_policy_version"]
        row = {"withdrawal_id": payload["withdrawal_id"],
               "tenant_node_id": ctx["tenant_node_id"], "case_id": case_id,
               "artifact_id": artifact["artifact_id"],
               "policy_version_seq": policy["access_policy_version"],
               "reason": payload["reason"], "review_todo_id": payload["review_todo_id"],
               "withdrawn_at": ctx["evaluated_at"],
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["output_withdrawals"].append(row)
        versions = [{"object_id": row["withdrawal_id"], "version_seq": 1}]
    elif action == DISPATCH:
        task = task_by_id(domain, cmd["target_object_id"])
        task.update(status="DISPATCHED", dispatched_at=ctx["evaluated_at"],
                    attempts=task["attempts"] + 1,
                    created_command_id=cmd["command_id"],
                    created_envelope_id=ctx["new_envelope_id"])
        versions = [{"object_id": task["notification_id"], "version_seq": 1}]
    elif action == RECOVERY:
        quarantine = derived_quarantine(payload)
        row = {"manifest_id": payload["manifest_id"],
               "tenant_node_id": ctx["tenant_node_id"], "case_id": case_id,
               "archive_id": payload["archive_id"],
               "material_completeness": payload["material_completeness"],
               "digest_status": payload["digest_status"],
               "source_auth_status": "NOT_SIGNED_BATCH1_BASELINE",
               "procedural_effect": payload["procedural_effect"],
               "external_control_rebinding": payload["external_control_rebinding"],
               "quarantine_required": quarantine,
               "records": copy.deepcopy(payload["records"]),
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["recovery_manifests"].append(row)
        versions = [{"object_id": row["manifest_id"], "version_seq": 1}]
    elif action == TEMPLATE:
        row = {"installation_id": payload["installation_id"],
               "tenant_node_id": ctx["tenant_node_id"], "case_id": case_id,
               "template_id": payload["template_id"],
               "template_version": payload["template_version"],
               "supported_contract_versions": copy.deepcopy(
                   payload["supported_contract_versions"]),
               "mode": "SIMULATION",
               "local_adoption_ref": copy.deepcopy(payload["local_adoption_ref"]),
               "field_mapping": copy.deepcopy(payload["field_mapping"]),
               "pending_local_verification": copy.deepcopy(
                   payload["pending_local_verification"]),
               "source_ref": copy.deepcopy(payload["source_ref"]),
               "created_command_id": cmd["command_id"],
               "created_envelope_id": ctx["new_envelope_id"]}
        domain["template_installations"].append(row)
        versions = [{"object_id": row["installation_id"], "version_seq": 1}]
    else:
        row = {"deployment_id": payload["deployment_id"],
               "tenant_node_id": ctx["tenant_node_id"], "case_id": case_id}
        for key in ("build_ref", "schema_ref", "action_registry_ref", "spec_frozen_ref",
                    "test_report_ref", "migration_plan_ref", "backup_recovery_ref",
                    "approval_ref", "rollback_build_ref"):
            row[key] = copy.deepcopy(payload[key])
        row.update(template_versions=copy.deepcopy(payload["template_versions"]),
                   target_environment=payload["target_environment"],
                   operator_member_id=payload["operator_member_id"],
                   compatibility_status=payload["compatibility_status"],
                   actions_enabled=payload["compatibility_status"] == "COMPATIBLE",
                   created_command_id=cmd["command_id"],
                   created_envelope_id=ctx["new_envelope_id"])
        domain["deployment_records"].append(row)
        versions = [{"object_id": row["deployment_id"], "version_seq": 1}]
    if action == PUBLISH:
        domain["effectiveness_envelopes"].append(envelope(data, action, versions,
                                                          target_id=versions[0]["object_id"]))
    elif action == DISPATCH:
        domain["effectiveness_envelopes"].append(envelope(data, action, versions,
                                                          target_id=versions[0]["object_id"]))
    else:
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
