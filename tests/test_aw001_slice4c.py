"""Slice 4C offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools import validate_aw001_slice4c as c

FIXTURES = c.ROOT / "fixtures/aw001_slice4/4c"
CASES = c.load_json(FIXTURES / "expectations.json")


def fixture(name):
    return c.load_json(FIXTURES / (name + ".json"))


class GoldenTests(unittest.TestCase):
    pass


def golden(name):
    def test(self):
        expected = CASES[name]
        result = (c.check_registry(FIXTURES / (name + ".json"))
                  if expected["validation_layer"] == "REGISTRY_LOADER"
                  else c.validate_transition(fixture(name)))
        self.assertEqual((result["is_valid"], result["validation_layer"], result["error_code"]),
                         (expected["error_code"] is None, expected["validation_layer"],
                          expected["error_code"]), result)
    return test


for name in CASES:
    setattr(GoldenTests, "test_" + name, golden(name))


class ReleaseTests(unittest.TestCase):
    def test_stage_records_private_file_without_publishing(self):
        data = fixture("pt_stage_output")
        staging = data["after"]["domain_objects"]["output_stagings"][0]
        self.assertEqual(staging["status"], "STAGED")
        self.assertIsNone(staging["published_file_ref"])
        self.assertIsNone(staging["released_at"])
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])

    def test_publish_requires_matching_policy_version(self):
        data = fixture("pt_publish_local_output")
        staging = data["after"]["domain_objects"]["output_stagings"][0]
        self.assertEqual(staging["status"], "PUBLISHED")
        self.assertIsNotNone(staging["published_file_ref"])
        stale = fixture("pt_rejected_publish_stale_policy")
        self.assertTrue(c.validate_transition(stale)["is_valid"])
        self.assertEqual(stale["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_OUTPUT_STALE_POLICY")

    def test_publish_after_withdrawal_is_rejected(self):
        data = fixture("pt_rejected_publish_withdrawn_artifact")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_OUTPUT_WITHDRAWN")

    def test_publish_does_not_move_files_or_touch_decisions(self):
        data = fixture("pt_publish_local_output")
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])
        self.assertEqual(data["after"]["domain_objects"]["output_withdrawals"], [])


class WithdrawalNotificationTests(unittest.TestCase):
    def test_withdrawal_masks_artifact_and_bumps_policy(self):
        data = fixture("pt_withdraw_output")
        domain = data["after"]["domain_objects"]
        artifact = domain["output_artifacts"][0]
        self.assertEqual(artifact["display_status"], "WITHDRAWN")
        self.assertEqual(domain["case_access_policies"][0]["access_policy_version"], 2)
        withdrawal = domain["output_withdrawals"][0]
        self.assertEqual(withdrawal["policy_version_seq"], 2)
        self.assertEqual(withdrawal["review_todo_id"], "review-todo-1")

    def test_withdrawal_does_not_pause_or_revoke_decisions(self):
        data = fixture("pt_withdraw_output")
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])

    def test_queued_full_text_is_not_dispatched_after_withdrawal(self):
        data = fixture("pt_rejected_dispatch_after_withdrawal")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_NOTIFICATION_STALE_PERMISSION")
        task = data["after"]["domain_objects"]["notification_tasks"][0]
        self.assertEqual(task["status"], "PENDING")

    def test_notification_dispatch_is_single_effect(self):
        data = fixture("pt_dispatch_notification")
        task = data["after"]["domain_objects"]["notification_tasks"][0]
        self.assertEqual(task["status"], "DISPATCHED")
        self.assertEqual(task["attempts"], 1)
        again = fixture("pt_rejected_dispatch_twice")
        self.assertEqual(again["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_NOTIFICATION_ALREADY_DISPATCHED")

    def test_metadata_only_dispatch_is_allowed(self):
        task = fixture("pt_dispatch_metadata_only")["after"]["domain_objects"][
            "notification_tasks"][1]
        self.assertEqual(task["status"], "DISPATCHED")


class RecoveryTemplateDeploymentTests(unittest.TestCase):
    def test_recovery_manifest_keeps_four_dimensions_separate(self):
        manifest = fixture("pt_recovery_manifest_complete")["after"]["domain_objects"][
            "recovery_manifests"][0]
        self.assertEqual(manifest["source_auth_status"], "NOT_SIGNED_BATCH1_BASELINE")
        self.assertEqual(manifest["procedural_effect"], "PRESERVED_READ_ONLY")
        self.assertEqual(manifest["external_control_rebinding"], "NOT_REQUIRED")
        self.assertFalse(manifest["quarantine_required"])

    def test_mismatched_recovery_is_quarantined_read_only(self):
        manifest = fixture("pt_recovery_manifest_quarantined")["after"]["domain_objects"][
            "recovery_manifests"][0]
        self.assertTrue(manifest["quarantine_required"])
        self.assertEqual(manifest["procedural_effect"], "REQUIRES_REAUTHORIZATION")
        self.assertTrue(all(record["read_only"] for record in manifest["records"]))

    def test_recovery_never_restores_execution(self):
        payload_keys = set(fixture("pt_recovery_manifest_complete")["command"]["payload"])
        self.assertNotIn("execution_enabled", payload_keys)
        self.assertNotIn("restored_actions", payload_keys)

    def test_template_install_is_simulation_and_not_authorization(self):
        data = fixture("pt_install_template")
        installation = data["after"]["domain_objects"]["template_installations"][0]
        self.assertEqual(installation["mode"], "SIMULATION")
        self.assertIn("0.5.0-rc1-slice4c", installation["supported_contract_versions"])
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])
        self.assertEqual(data["after"]["domain_objects"]["template_installations"][0]
                         ["pending_local_verification"], ["local legal review"])

    def test_unsupported_template_contract_is_rejected(self):
        data = fixture("pt_rejected_template_contract_unsupported")
        self.assertEqual(data["after"]["execution_outcome"]["command_rejection"][
                             "rejection_error_code"], "ERR_TEMPLATE_CONTRACT_UNSUPPORTED")

    def test_deployment_manifest_does_not_change_charter_or_decisions(self):
        data = fixture("pt_record_deployment")
        record = data["after"]["domain_objects"]["deployment_records"][0]
        self.assertEqual(record["compatibility_status"], "COMPATIBLE")
        self.assertTrue(record["actions_enabled"])
        self.assertEqual(data["after"]["domain_objects"]["operations"],
                         data["before"]["domain_objects"]["operations"])

    def test_incompatible_migration_disables_new_actions(self):
        record = fixture("pt_record_incompatible_deployment")["after"]["domain_objects"][
            "deployment_records"][0]
        self.assertEqual(record["compatibility_status"], "INCOMPATIBLE")
        self.assertFalse(record["actions_enabled"])


class BoundaryTests(unittest.TestCase):
    def test_cli_valid_and_invalid_exit_codes(self):
        for name, code in [("pt_publish_local_output", 0), ("ft_schema_release_now", 1)]:
            proc = subprocess.run([sys.executable, str(c.ROOT / "tools/validate_aw001_slice4c.py"),
                                   str(FIXTURES / (name + ".json"))],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["is_valid"], code == 0)

    def test_registry_rejects_relaxed_checks_and_lock_order(self):
        registry = c.load_json(c.SPEC / "fixed-action-registry.json")
        for mutation in ["validators", "lock_order"]:
            candidate = copy.deepcopy(registry)
            if mutation == "validators":
                candidate["actions"]["PUBLISH_LOCAL_OUTPUT"]["required_validators"].remove(
                    "CHECK_OUTPUT_RELEASE")
            else:
                candidate["global_lock_order"] = list(reversed(candidate["global_lock_order"]))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "registry.json"
                path.write_text(json.dumps(candidate))
                result = c.check_registry(path)
                self.assertFalse(result["is_valid"])
                self.assertEqual(result["validation_layer"], "REGISTRY_LOADER")

    def test_simulation_only(self):
        for key, value in [("mode", "delegated"), ("execution_enabled", True),
                           ("data_classification", "real")]:
            data = fixture("pt_stage_output")
            data["server_context"][key] = value
            result = c.validate_transition(data)
            self.assertFalse(result["is_valid"], key)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA")

    def test_cross_slice_operations_are_untouched(self):
        for name, expected in CASES.items():
            if expected["error_code"] is not None or expected["validation_layer"] == "REGISTRY_LOADER":
                continue
            data = fixture(name)
            if data["after"]["execution_outcome"]["outcome"] != "APPLIED":
                continue
            self.assertEqual(data["after"]["domain_objects"]["operations"],
                             data["before"]["domain_objects"]["operations"], name)

    def test_client_cannot_claim_release_execution_or_override(self):
        for name in ["ft_schema_release_now", "ft_schema_execution_restore",
                     "ft_schema_charter_override", "ft_schema_template_authorization"]:
            result = c.validate_transition(fixture(name))
            self.assertFalse(result["is_valid"], name)
            self.assertEqual(result["validation_layer"], "JSON_SCHEMA", name)

    def test_schema_layer_counts_are_stable(self):
        layers = {}
        for expected in CASES.values():
            layers[expected["validation_layer"]] = layers.get(expected["validation_layer"], 0) + 1
        self.assertEqual(layers.get("REGISTRY_LOADER", 0), 3)
        self.assertGreaterEqual(layers.get("OFFLINE_SEMANTIC", 0), 25)


if __name__ == "__main__":
    unittest.main()
