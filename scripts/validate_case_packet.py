"""Offline checks for synthetic gate-review snapshots; never authorizes an action."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def utc_seconds(value):
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        return False
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return True


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        reject_constant(value)
    return number


def load_json(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


SCHEMA = load_json(ROOT / "specs/case-packet.schema.json")
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FORMATS)


def validate_packet(packet):
    errors = [f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
              for error in VALIDATOR.iter_errors(packet)]
    if errors:
        return errors

    def index(rows, key):
        result = {}
        for row in rows:
            if row[key] in result:
                errors.append(f"duplicate {key}: {row[key]}")
            result[row[key]] = row
        return result

    evidence = index(packet["evidence"], "evidence_id")
    authorities = index(packet["authority_records"], "authority_id")
    index(packet["constraints"], "constraint_id")
    index(packet["gates"], "gate_id")
    audit = packet["inclusion_audit"]
    index(audit["strata"], "group_id")

    def check_ref(ref):
        item = evidence.get(ref["evidence_id"])
        if item is None or item["revision"] != ref["revision"]:
            errors.append(f"unresolved evidence revision: {ref}")

    for ref in (packet["charter_ref"], packet["rules_ref"], audit["assessment_ref"]):
        check_ref(ref)
    for authority in authorities.values():
        check_ref(authority["basis_ref"])
        if authority["valid_from"] >= authority["valid_until"]:
            errors.append(f"invalid authority interval: {authority['authority_id']}")
    for constraint in packet["constraints"]:
        check_ref(constraint["source_ref"])
        if (packet["review_status"] == "ready_for_round"
                and constraint["status"] in {"asserted", "contested"}):
            errors.append(f"ready_for_round has unresolved constraint: {constraint['constraint_id']}")
        disposition = constraint["disposition_ref"]
        if disposition is not None:
            check_ref(disposition)
        elif constraint["status"] in {"adopted", "inapplicable"}:
            errors.append(f"constraint requires disposition: {constraint['constraint_id']}")

    if not audit["window_start"] <= audit["window_end"] <= packet["exported_at"]:
        errors.append("invalid inclusion audit interval")
    for counts in [audit["counts"]] + [group["counts"] for group in audit["strata"]]:
        if counts["participating"] > counts["responded"]:
            errors.append("participating exceeds responded")
        if counts["invited"] is not None and counts["responded"] > counts["invited"]:
            errors.append("responded exceeds invited")
    population = audit["eligible_population"]
    total = audit["counts"]
    if population is not None and max(total["responded"], total["invited"] or 0) > population:
        errors.append("recruitment count exceeds eligible population")
    for key in ("invited", "responded", "participating"):
        values = [group["counts"][key] for group in audit["strata"]]
        if total[key] is not None:
            if any(value is not None and value > total[key] for value in values):
                errors.append(f"stratum exceeds total {key}")
            if not audit["strata_may_overlap"] and sum(value or 0 for value in values) > total[key]:
                errors.append(f"disjoint strata exceed total {key}")

    gates = packet["gates"]
    if {gate["kind"] for gate in gates} != {"participation", "constraints", "mandate"}:
        errors.append("exactly one of each required gate kind must be present")
    all_effective = True
    for gate in gates:
        for ref in gate["evidence_refs"]:
            check_ref(ref)
        authority = authorities.get(gate["authority_ref"])
        if gate["authority_ref"] is not None and authority is None:
            errors.append(f"unresolved authority: {gate['authority_ref']}")
        assessed, until = gate["assessed_at"], gate["valid_until"]
        if assessed is not None and assessed > packet["exported_at"]:
            errors.append(f"assessment after export: {gate['gate_id']}")
        if assessed is not None and until is not None and assessed >= until:
            errors.append(f"invalid gate interval: {gate['gate_id']}")
        if gate["case_revision"] > packet["case_version"]:
            errors.append(f"gate cites future case revision: {gate['gate_id']}")
        if gate["status"] != "unknown":
            if not (authority and gate["reviewer_id"] and assessed and until and gate["evidence_refs"]):
                errors.append(f"assessed gate lacks attribution or evidence: {gate['gate_id']}")
            elif (authority["actor_id"] != gate["reviewer_id"]
                  or f"assess_{gate['kind']}" not in authority["powers"]
                  or not authority["valid_from"] <= assessed < authority["valid_until"]):
                errors.append(f"gate exceeds declared assessment authority: {gate['gate_id']}")
        effective = bool(
            gate["status"] == "pass" and authority
            and authority["status"] == "active"
            and authority["valid_from"] <= packet["exported_at"] < authority["valid_until"]
            and until and packet["exported_at"] < until
            and gate["case_revision"] == packet["case_version"]
        )
        all_effective = all_effective and effective
    if packet["review_status"] == "ready_for_round" and not all_effective:
        errors.append("ready_for_round requires three current, authorized pass assessments")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets", type=Path, nargs="+")
    args = parser.parse_args()
    failed = False
    for path in args.packets:
        try:
            packet = load_json(path)
            errors = validate_packet(packet)
        except (OSError, ValueError) as error:
            errors = [str(error)]
        if errors:
            failed = True
            print(f"INVALID {path}")
            for error in errors:
                print(f"  {error}")
        else:
            print(f"VALID {path} ({packet['review_status']}; synthetic snapshot only; no authorization)")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
