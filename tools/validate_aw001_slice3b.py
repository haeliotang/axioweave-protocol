"""Slice 3B offline synthetic metric-window coverage and directional uncertainty.

Validation only: never samples reality, never interpolates, and never starts the
application or opens a ballot.
"""

import argparse
import copy
import datetime
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
SPEC = ROOT / "specs/aw001_slice3/3b"
DEFAULT_REGISTRY = SPEC / "fixed-action-registry.json"
SCHEMA = load_json(SPEC / "transition-fixture.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)
COMMON = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/FixtureCommon"}, format_checker=FORMATS)
DOMAIN = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/Domain"}, format_checker=FORMATS)

DEFINE, EVALUATE, EXTEND = ("DEFINE_OBSERVABLE_METRIC", "EVALUATE_METRIC_WINDOW",
                            "EXTEND_METRIC_WINDOW")
ACTIONS = (DEFINE, EVALUATE, EXTEND)
POWERS = {DEFINE: "DEFINE_OBSERVABLE_METRIC", EVALUATE: "EVALUATE_METRIC_WINDOW",
          EXTEND: "EXTEND_METRIC_WINDOW"}
PAYLOADS = {DEFINE: "Payload0", EVALUATE: "Payload1", EXTEND: "Payload2"}
CHECKS = ["CHECK_CONTEXT_AND_VERSIONS", "CHECK_COMMAND_COORDINATION",
          "CHECK_INTAKE_OR_FOUR_TUPLE", "CHECK_METRIC_WINDOW_AND_BUCKETS",
          "CHECK_METRIC_LEDGER", "CHECK_ALLOWED_WRITE_SET",
          "CHECK_RESULT_AND_AUDIT_BINDING"]
WRITES = {DEFINE: "CREATE_OBSERVABLE_METRIC_SPEC",
          EVALUATE: "CREATE_METRIC_EVALUATION", EXTEND: "EXTEND_METRIC_EVALUATION"}
LOCK_ORDER = ["CommandRecord", "AuthorityRecord", "Case / Commitment", "ProtectiveOrder",
              "FulfillmentTicket"]
STATUS_CODES = {"SOURCE_UNAVAILABLE": "ERR_MANDATE_SOURCE_UNAVAILABLE",
                "EXPIRED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "REVOKED": "ERR_MANDATE_LAPSED_OR_REVOKED",
                "POWER_NOT_GRANTED": "ERR_UNAUTHORIZED_POWER",
                "SCOPE_MISMATCH": "ERR_MANDATE_SCOPE_MISMATCH",
                "UNKNOWN_MANDATE": "ERR_UNKNOWN_MANDATE"}
INTERVAL_TYPES = {"MEASUREMENT_UNCERTAINTY", "STATISTICAL_CONFIDENCE_INTERVAL",
                  "OTHER_DECLARED"}


def pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def epoch(timestamp):
    return int(datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp())


def stamp(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


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
    require(value["contract_version"] == "0.4.0-rc1-slice3b",
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


# ---------------------------------------------------------------- indexing
def object_index(domain):
    index = {}
    for name in ("cases", "resource_pools", "commitments", "tickets"):
        for row in domain["operations"][name]:
            index[row["object_id"]] = row
    for row in domain["operations"]["protective_orders"]:
        index[row["order_id"]] = {**row, "object_id": row["order_id"],
                                  "kind": "PROTECTIVE_ORDER"}
    for row in domain["observable_metrics"]:
        index[row["metric_id"]] = row
    for row in domain["metric_evaluations"]:
        index[row["evaluation_id"]] = row
    return index


def member_mapping(ctx, principal):
    row = next((m for m in ctx["member_directory"] if m["principal_id"] == principal), None)
    return row["member_id"] if row and row["status"] == "VERIFIED" else None


def evidence_ref_resolved(ctx, ref):
    return ref in ctx["evidence_snapshots"]


# ---------------------------------------------------------------- derivation
def window_grid(window, bucket_seconds):
    start, end = epoch(window["start"]), epoch(window["end"])
    require(end > start, "ERR_METRIC_WINDOW_NOT_ALIGNED", "/observation_window")
    require(start % bucket_seconds == 0 and end % bucket_seconds == 0,
            "ERR_METRIC_WINDOW_NOT_ALIGNED", "/observation_window")
    require((end - start) % bucket_seconds == 0, "ERR_METRIC_WINDOW_NOT_ALIGNED",
            "/observation_window")
    return start, end, (end - start) // bucket_seconds


def validate_observations(spec, window, observations):
    start, end = epoch(window["start"]), epoch(window["end"])
    seen = {}
    prepared = []
    for index, observation in enumerate(observations):
        observed, received = epoch(observation["observed_at"]), epoch(observation["received_at"])
        require(start <= observed < end, "ERR_METRIC_OBSERVATION_OUTSIDE_WINDOW",
                "/observations/" + str(index))
        require(received >= observed, "ERR_METRIC_OBSERVATION_TIME_INVALID",
                "/observations/" + str(index))
        duplicate = False
        if spec["dedupe_rule"] == "BY_DEDUPE_KEY":
            require(observation["dedupe_key"] is not None, "ERR_METRIC_DEDUPE_KEY_MISSING",
                    "/observations/" + str(index))
            key = observation["dedupe_key"]
            if key in seen:
                duplicate = True
            else:
                seen[key] = observation["observation_id"]
        prepared.append((observation, duplicate))
    return prepared


def derive_metric_result(spec, window, observations, rule, uncertainty, point_estimate,
                         evaluated_at):
    start, end, total_buckets = window_grid(window, spec["bucket_seconds"])
    prepared = validate_observations(spec, window, observations)
    minimum = spec["min_samples_per_valid_bucket"]
    entries = []
    for index in range(total_buckets):
        bucket_start = start + index * spec["bucket_seconds"]
        bucket_end = bucket_start + spec["bucket_seconds"]
        sample_count = rejected = duplicate = valid = 0
        for observation, is_duplicate in prepared:
            observed = epoch(observation["observed_at"])
            if not bucket_start <= observed < bucket_end:
                continue
            sample_count += 1
            if observation["quality"] == "REJECTED":
                rejected += 1
            elif is_duplicate:
                duplicate += 1
            else:
                valid += 1
        entries.append({"bucket_start": stamp(bucket_start), "sample_count": sample_count,
                        "rejected_count": rejected, "duplicate_count": duplicate,
                        "valid_sample_count": valid, "valid": valid >= minimum})
    require(sum(row["sample_count"] for row in entries) == len(observations),
            "ERR_METRIC_LEDGER_INCONSISTENT", "/observations")
    valid_buckets = sum(1 for row in entries if row["valid"])
    invalid_idx = [i for i, row in enumerate(entries) if not row["valid"]]
    total = total_buckets
    coverage_ppm = valid_buckets * 1000000 // total
    if valid_buckets == 0:
        leading, internal, trailing = total, 0, 0
    else:
        first_valid = next(i for i, row in enumerate(entries) if row["valid"])
        last_valid = max(i for i, row in enumerate(entries) if row["valid"])
        leading = first_valid
        trailing = total - 1 - last_valid
        runs, current = [], 0
        for i in range(first_valid + 1, last_valid):
            if not entries[i]["valid"]:
                current += 1
            else:
                runs.append(current)
                current = 0
        runs.append(current)
        internal = max(runs) if runs else 0
    coverage = {"total_buckets": total, "valid_buckets": valid_buckets,
                "invalid_buckets": total - valid_buckets, "coverage_ppm": coverage_ppm,
                "leading_gap_buckets": leading, "internal_gap_buckets": internal,
                "trailing_gap_buckets": trailing}
    if evaluated_at < window["end"]:
        coverage_status, direction, result = "NOT_YET_EVALUATED", None, "NOT_YET_EVALUATED"
    elif coverage_ppm < spec["min_valid_bucket_coverage_ppm"]:
        coverage_status = "INDETERMINATE_INSUFFICIENT_COVERAGE"
        direction, result = None, "INDETERMINATE_INSUFFICIENT_COVERAGE"
    else:
        coverage_status = "COVERAGE_SUFFICIENT"
        direction = direction_outcome(rule, uncertainty)
        result = direction
    return {"buckets": entries, "coverage": coverage, "coverage_status": coverage_status,
            "direction_outcome": direction, "result_status": result}


def direction_outcome(rule, uncertainty):
    lower, upper = uncertainty["lower_minor"], uncertainty["upper_minor"]
    operator = rule["operator"]
    if operator in {"LT", "LTE", "GT", "GTE"}:
        threshold = rule["threshold_minor"]
        if operator == "LT":
            if upper < threshold:
                return "SATISFIED"
            return "VIOLATED" if lower >= threshold else "INDETERMINATE_UNCERTAINTY"
        if operator == "LTE":
            if upper <= threshold:
                return "SATISFIED"
            return "VIOLATED" if lower > threshold else "INDETERMINATE_UNCERTAINTY"
        if operator == "GT":
            if lower > threshold:
                return "SATISFIED"
            return "VIOLATED" if upper <= threshold else "INDETERMINATE_UNCERTAINTY"
        if lower >= threshold:
            return "SATISFIED"
        return "VIOLATED" if upper < threshold else "INDETERMINATE_UNCERTAINTY"
    low, high = rule["band_low_minor"], rule["band_high_minor"]
    if operator == "WITHIN_BAND":
        if lower >= low and upper <= high:
            return "SATISFIED"
        return "VIOLATED" if upper < low or lower > high else "INDETERMINATE_UNCERTAINTY"
    if upper < low or lower > high:
        return "SATISFIED"
    return "VIOLATED" if lower >= low and upper <= high else "INDETERMINATE_UNCERTAINTY"


def validate_rule_and_uncertainty(spec, rule, uncertainty, point_estimate):
    if rule["operator"] in {"WITHIN_BAND", "OUTSIDE_BAND"}:
        require(rule["band_low_minor"] < rule["band_high_minor"], "ERR_METRIC_BAND_INVALID",
                "/rule")
    require(uncertainty["lower_minor"] <= uncertainty["upper_minor"],
            "ERR_METRIC_INTERVAL_INVALID", "/uncertainty")
    require(uncertainty["interval_type"] == spec["uncertainty_interval_type"],
            "ERR_METRIC_INTERVAL_INVALID", "/uncertainty/interval_type")
    if uncertainty["interval_type"] == "STATISTICAL_CONFIDENCE_INTERVAL":
        require(uncertainty["confidence_ppm"] is not None, "ERR_METRIC_INTERVAL_INVALID",
                "/uncertainty/confidence_ppm")
    else:
        require(uncertainty["confidence_ppm"] is None, "ERR_METRIC_INTERVAL_INVALID",
                "/uncertainty/confidence_ppm")
    if point_estimate is not None:
        require(spec["preapproved_point_estimate"], "ERR_POINT_ESTIMATE_NOT_PREAPPROVED",
                "/point_estimate_minor")


# ---------------------------------------------------------------- domain
def evaluations_by_metric(domain):
    result = {}
    for row in domain["metric_evaluations"]:
        result.setdefault(row["metric_id"], []).append(row)
    return result


def validate_domain(domain):
    require(DOMAIN.is_valid(domain), "ERR_INVALID_DOMAIN_SCHEMA", "/domain_objects")
    b.validate_domain(domain["operations"])
    specs = {row["metric_id"]: row for row in domain["observable_metrics"]}
    require(len(specs) == len(domain["observable_metrics"]), "ERR_DUPLICATE_OBJECT_ID",
            "/observable_metrics")
    ids = [row["metric_id"] for row in domain["observable_metrics"]] + \
          [row["evaluation_id"] for row in domain["metric_evaluations"]] + \
          [row["envelope_id"] for row in domain["effectiveness_envelopes"]]
    operation_ids = [row["object_id"] for name in
                     ("cases", "resource_pools", "commitments", "tickets")
                     for row in domain["operations"][name]]
    operation_ids += [row["order_id"] for row in domain["operations"]["protective_orders"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["protection_envelopes"]]
    operation_ids += [row["envelope_id"] for row in domain["operations"]["effectiveness_envelopes"]]
    require(len(ids) == len(set(ids)) and not set(ids) & set(operation_ids),
            "ERR_DUPLICATE_OBJECT_ID", "/domain_objects")
    envelopes = {row["envelope_id"]: row for row in domain["effectiveness_envelopes"]}
    for envelope in domain["effectiveness_envelopes"]:
        require(envelope["effect_parameters_sha256"] == digest(envelope["payload_snapshot"]),
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
        require(validate_payload({"requested_action": envelope["action_type"],
                                  "payload": envelope["payload_snapshot"]}) is None,
                "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
        for version in envelope["resulting_versions"]:
            target = object_index(domain).get(version["object_id"])
            require(target is not None and version["version_seq"] <= target["version_seq"],
                    "ERR_INVALID_EFFECT_HISTORY", "/effectiveness_envelopes")
    referenced = set()
    for spec in domain["observable_metrics"]:
        creation = envelopes.get(spec["created_envelope_id"])
        require(creation is not None and creation["action_type"] == DEFINE
                and {"object_id": spec["metric_id"], "version_seq": 1}
                in creation["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                "/observable_metrics")
        referenced.add(spec["created_envelope_id"])
    grouped = evaluations_by_metric(domain)
    for metric_id, rows in grouped.items():
        spec = specs.get(metric_id)
        require(spec is not None and spec["case_id"] == rows[0]["case_id"],
                "ERR_INVALID_EFFECT_HISTORY", "/metric_evaluations")
        rows = sorted(rows, key=lambda r: r["version_seq"])
        require([r["version_seq"] for r in rows] == list(range(1, len(rows) + 1)),
                "ERR_METRIC_EVALUATION_VERSION_CONFLICT", "/metric_evaluations")
        previous = None
        for row in rows:
            require(row["metric_id"] == metric_id, "ERR_INVALID_EFFECT_HISTORY",
                    "/metric_evaluations")
            if previous is None:
                require(row["supersedes_evaluation_id"] is None,
                        "ERR_METRIC_SUPERSEDES_MISMATCH", "/metric_evaluations")
            else:
                require(row["supersedes_evaluation_id"] == previous["evaluation_id"],
                        "ERR_METRIC_SUPERSEDES_MISMATCH", "/metric_evaluations")
                require(epoch(row["observation_window"]["start"])
                        <= epoch(previous["observation_window"]["start"])
                        and epoch(row["observation_window"]["end"])
                        > epoch(previous["observation_window"]["end"]),
                        "ERR_METRIC_WINDOW_NOT_EXTENDED", "/metric_evaluations")
            validate_rule_and_uncertainty(spec, row["rule"], row["uncertainty"],
                                          row["point_estimate_minor"])
            derived = derive_metric_result(spec, row["observation_window"], row["observations"],
                                           row["rule"], row["uncertainty"],
                                           row["point_estimate_minor"], row["evaluated_at"])
            require(row["buckets"] == derived["buckets"], "ERR_METRIC_LEDGER_INCONSISTENT",
                    "/metric_evaluations")
            require(row["coverage"] == derived["coverage"]
                    and row["coverage_status"] == derived["coverage_status"]
                    and row["direction_outcome"] == derived["direction_outcome"]
                    and row["result_status"] == derived["result_status"],
                    "ERR_METRIC_LEDGER_INCONSISTENT", "/metric_evaluations")
            envelope = envelopes.get(row["envelope_id"])
            expected_action = EVALUATE if row["version_seq"] == 1 else EXTEND
            require(envelope is not None and envelope["action_type"] == expected_action
                    and envelope["target_object_id"] == metric_id
                    and {"object_id": row["evaluation_id"],
                         "version_seq": row["version_seq"]}
                    in envelope["resulting_versions"], "ERR_INVALID_EFFECT_HISTORY",
                    "/metric_evaluations")
            referenced.add(row["envelope_id"])
            previous = row
    require(referenced == set(envelopes), "ERR_INVALID_EFFECT_HISTORY",
            "/effectiveness_envelopes")
    return object_index(domain)


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
    expected_kind = "CASE" if cmd["requested_action"] == DEFINE else "METRIC"
    kind = ("CASE" if target.get("kind") == "CASE" else
            "METRIC" if "metric_id" in target else None)
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
    if cmd["requested_action"] == DEFINE:
        refs.append(cmd["payload"]["source_ref"])
    else:
        refs.append(cmd["payload"]["uncertainty"]["computation_source_ref"])
        spec = next(row for row in before["domain_objects"]["observable_metrics"]
                    if row["metric_id"] == cmd["target_object_id"])
        refs.append(spec["source_ref"])
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
        if action == DEFINE:
            require(payload["metric_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
                    "/command/payload/metric_id")
        else:
            metric_id = cmd["target_object_id"]
            rows = [row for row in data["before"]["domain_objects"]["metric_evaluations"]
                    if row["metric_id"] == metric_id]
            require(all(row["evaluation_id"] != payload["evaluation_id"] for row in rows),
                    "ERR_NEW_OBJECT_ID_CONFLICT", "/command/payload/evaluation_id")
            spec = target
            if action == EVALUATE:
                require(not rows, "ERR_METRIC_EVALUATION_VERSION_CONFLICT",
                        "/domain_objects/metric_evaluations")
                require(payload["evaluation_id"] not in objects, "ERR_NEW_OBJECT_ID_CONFLICT",
                        "/command/payload/evaluation_id")
            else:
                require(rows, "ERR_METRIC_EVALUATION_VERSION_CONFLICT",
                        "/domain_objects/metric_evaluations")
                latest = max(rows, key=lambda r: r["version_seq"])
                require(payload["supersedes_evaluation_id"] == latest["evaluation_id"],
                        "ERR_METRIC_SUPERSEDES_MISMATCH",
                        "/command/payload/supersedes_evaluation_id")
                require(epoch(payload["observation_window"]["start"])
                        <= epoch(latest["observation_window"]["start"])
                        and epoch(payload["observation_window"]["end"])
                        > epoch(latest["observation_window"]["end"]),
                        "ERR_METRIC_WINDOW_NOT_EXTENDED", "/command/payload/observation_window")
            validate_rule_and_uncertainty(spec, payload["rule"], payload["uncertainty"],
                                          payload["point_estimate_minor"])
            derive_metric_result(spec, payload["observation_window"], payload["observations"],
                                 payload["rule"], payload["uncertainty"],
                                 payload["point_estimate_minor"], ctx["evaluated_at"])
    except ContractFailure as error:
        return [error.report["error_code"]]
    return []


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
    if action == DEFINE:
        spec = {"metric_id": payload["metric_id"], "tenant_node_id": ctx["tenant_node_id"],
                "case_id": ctx["case_id"], "version_seq": 1,
                "indicator_code": payload["indicator_code"],
                "unit_code": payload["unit_code"], "bucket_seconds": payload["bucket_seconds"],
                "min_valid_bucket_coverage_ppm": payload["min_valid_bucket_coverage_ppm"],
                "min_samples_per_valid_bucket": payload["min_samples_per_valid_bucket"],
                "dedupe_rule": payload["dedupe_rule"],
                "late_revision_policy": "NEW_VERSION_ONLY",
                "interpolation_policy": "FORBIDDEN",
                "uncertainty_interval_type": payload["uncertainty_interval_type"],
                "preapproved_point_estimate": payload["preapproved_point_estimate"],
                "source_ref": copy.deepcopy(payload["source_ref"]),
                "created_command_id": cmd["command_id"],
                "created_envelope_id": ctx["new_envelope_id"]}
        domain["observable_metrics"].append(spec)
        versions = [{"object_id": spec["metric_id"], "version_seq": 1}]
    else:
        spec = next(row for row in domain["observable_metrics"]
                    if row["metric_id"] == cmd["target_object_id"])
        rows = [row for row in domain["metric_evaluations"]
                if row["metric_id"] == spec["metric_id"]]
        version = 1 if action == EVALUATE else max(r["version_seq"] for r in rows) + 1
        derived = derive_metric_result(spec, payload["observation_window"],
                                       payload["observations"], payload["rule"],
                                       payload["uncertainty"], payload["point_estimate_minor"],
                                       ctx["evaluated_at"])
        evaluation = {"evaluation_id": payload["evaluation_id"],
                      "metric_id": spec["metric_id"], "tenant_node_id": ctx["tenant_node_id"],
                      "case_id": ctx["case_id"], "version_seq": version,
                      "supersedes_evaluation_id": payload.get("supersedes_evaluation_id"),
                      "observation_window": copy.deepcopy(payload["observation_window"]),
                      "evaluated_at": ctx["evaluated_at"],
                      "observations": copy.deepcopy(payload["observations"]),
                      "buckets": derived["buckets"], "coverage": derived["coverage"],
                      "rule": copy.deepcopy(payload["rule"]),
                      "uncertainty": copy.deepcopy(payload["uncertainty"]),
                      "point_estimate_minor": payload["point_estimate_minor"],
                      "coverage_status": derived["coverage_status"],
                      "direction_outcome": derived["direction_outcome"],
                      "result_status": derived["result_status"],
                      "interpolation_used": False,
                      "created_command_id": cmd["command_id"],
                      "envelope_id": ctx["new_envelope_id"]}
        domain["metric_evaluations"].append(evaluation)
        versions = [{"object_id": evaluation["evaluation_id"], "version_seq": version}]
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
