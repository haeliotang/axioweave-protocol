"""Slice 2C offline contracts: expectations are authored independently of validation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tools import validate_aw001_slice2c as c

FIXTURES = c.ROOT / 'fixtures/aw001_slice2/2c'
CASES = c.load_json(FIXTURES / 'expectations.json')


def fixture(name):
    return c.load_json(FIXTURES / (name + '.json'))


class GoldenTests(unittest.TestCase):
    pass


def golden(name):
    def test(self):
        expected = CASES[name]
        result = c.check_registry(FIXTURES / (name + '.json')) if expected['validation_layer'] == 'REGISTRY_LOADER' else c.validate_transition(fixture(name))
        self.assertEqual((result['is_valid'], result['validation_layer'], result['error_code']), (expected['error_code'] is None, expected['validation_layer'], expected['error_code']), result)
    return test


for name in CASES:
    setattr(GoldenTests, 'test_' + name, golden(name))


if __name__ == '__main__':
    unittest.main()


class CrossSliceTests(unittest.TestCase):
    def test_collective_approval_through_complete_two_phase_2b_flow(self):
        full = fixture('pt_collective_approve')['after']['domain_objects']
        previous = c.project_operations(full)
        self.assertEqual(len(full['procedure_envelopes'][0]['collective_member_attestations']), 3)
        amounts = [0, 0, 0, 2500, 2500, 4000, 4000, 4000, 4000, 10000]
        for i, expected_amount in enumerate(amounts, 1):
            data = fixture(f'cross_2b_{i:02d}')
            with self.subTest(step=i):
                self.assertEqual(data['before']['domain_objects'], previous)
                result = c.b.validate_transition(data)
                self.assertTrue(result['is_valid'], result)
                previous = data['after']['domain_objects']
                self.assertEqual(previous['commitments'][0]['verified_fulfilled_minor'], expected_amount)
                full['operations'] = copy.deepcopy(previous)
                self.assertEqual(c.project_operations(full), previous)
        self.assertEqual(previous['commitments'][0]['commitment_status'], 'COMPLETED')
        self.assertEqual(previous['resource_pools'][0]['committed_obligations_minor'], 10000)
        self.assertEqual(len(full['procedure_envelopes'][0]['collective_member_attestations']), 3)

    def test_appeal_stages_do_not_affect_2a_2b_protection_predicate(self):
        stages = ['pt_submit_with_active_po', 'pt_open_with_active_po', 'pt_rule_with_active_po', 'pt_adjudicate_with_active_po']
        expected_stages = ['SUBMITTED', 'ADMISSIBILITY_REVIEW', 'ADJUDICATING', 'RESOLVED']
        for name, stage in zip(stages, expected_stages):
            domain = fixture(name)['after']['domain_objects']
            self.assertEqual(domain['appeals'][0]['appeal_status'], stage)
            self.assertEqual(fixture(name)['before']['domain_objects']['operations'], domain['operations'])
            ops = c.project_operations(domain)
            # 2A has no RECORD_TICKET_HANDOFF power. Compare the shared AUTHORIZE
            # predicate using genuine 2A-issued/lifted orders, without relabelling powers.
            for order_fixture in ['pt_issue', 'pt_second_order', 'pt_lift_one_of_two', 'pt_extend_new_order']:
                source = c.load_json(c.ROOT / 'fixtures/aw001_slice2/2a' / (order_fixture + '.json'))['after']['domain_objects']
                source['resources'][3]['parent_object_id'] = 'commitment-1'
                ops['protective_orders'] = copy.deepcopy(source['protective_orders'])
                ops['protection_envelopes'] = copy.deepcopy(source['effectiveness_envelopes'])
                for now in ['2026-11-06T04:00:00Z', '2026-11-10T04:00:00Z']:
                    self.assertEqual(c.b.evaluate_protective_orders(ops, 'ticket-1', c.b.AUTHORIZE, now),
                                     c.b.a.evaluate_protective_orders(source, 'ticket-1', c.b.AUTHORIZE, now))
        active = c.project_operations(fixture(stages[-1])['after']['domain_objects'])
        self.assertEqual(c.b.evaluate_protective_orders(active, 'ticket-1', c.b.HANDOFF, '2026-11-06T04:00:00Z'), ['po-1'])
        self.assertEqual(c.b.evaluate_protective_orders(active, 'ticket-1', c.b.HANDOFF, '2026-11-10T04:00:00Z'), [])

    def test_explicit_policy_controls_parent_approvers(self):
        off = fixture('pt_parent_approver_policy_disabled')['before']['domain_objects']
        on = fixture('pt_rejected_parent_approver_policy_enabled')['before']['domain_objects']
        off_members, _ = c.recusal(off, off['appeals'][0], off['case_policies'][0]['sod_recusal_policy'])
        on_members, _ = c.recusal(on, on['appeals'][0], on['case_policies'][0]['sod_recusal_policy'])
        self.assertEqual(on_members - off_members, {'member-approver'})
        self.assertNotIn('member-proposer', on_members)
        self.assertNotIn('member-executor', on_members)

    def test_replaced_authorizer_and_collective_signers_remain_identifiable(self):
        d = fixture('pt_rejected_historical_replaced_authorizer')['before']['domain_objects']
        members, trace = c.recusal(d, d['appeals'][0], d['case_policies'][0]['sod_recusal_policy'])
        self.assertTrue({'member-finance', 'member-review3'} <= members)
        self.assertTrue(any(r['member_id'] == 'member-finance' and r['reason'] == 'DIRECT_AUTHORIZER_SIGNER' for r in trace))
        d = fixture('pt_open_collective_commitment')['before']['domain_objects']
        members, _ = c.recusal(d, d['appeals'][0], d['case_policies'][0]['sod_recusal_policy'])
        self.assertEqual(members, {'member-appellant', 'member-approver', 'member-approver2', 'member-approver3'})

    def test_qualified_pool_counts_members_not_aliases_or_unauthorized_people(self):
        for name in ['pt_deferral_registrar', 'pt_pool_alias_not_extra_reviewer']:
            f = fixture(name); objects, target, policy = c.validate_basis(f)
            codes, _, _, eligible = c.decision_diagnostics(f, objects, target, policy, c.current_receipt(f))
            self.assertEqual(codes, [])
            self.assertEqual(eligible, {'member-review1', 'member-review2'})
            self.assertEqual(f['after']['domain_objects']['appeals'][0]['appeal_status'], 'ADJUDICATING')
            self.assertEqual(f['before']['domain_objects']['operations'], f['after']['domain_objects']['operations'])


class BoundaryTests(unittest.TestCase):
    def assert_code(self, data, code):
        self.assertEqual(c.validate_transition(data)['error_code'], code)

    def test_historical_merits_cannot_disagree_with_its_envelope(self):
        d = fixture('pt_adjudicate_denied')['after']['domain_objects']
        d['appeals'][0]['merits_adjudication_record']['outcome'] = 'UPHELD_REMEDY_REQUIRED'
        with self.assertRaises(c.ContractFailure) as caught:
            c.validate_domain(d)
        self.assertEqual(caught.exception.report['error_code'], 'ERR_INVALID_APPEAL_HISTORY')

    def test_historical_member_receipt_scope_is_not_unchecked(self):
        d = fixture('pt_collective_approve')['after']['domain_objects']
        d['procedure_envelopes'][0]['collective_member_attestations'][2]['member_auth_receipt']['target_object_id'] = 'other-target'
        with self.assertRaises(c.ContractFailure) as caught:
            c.validate_domain(d)
        self.assertEqual(caught.exception.report['error_code'], 'ERR_INVALID_EFFECT_HISTORY')

    def test_cli_reports_exact_json_and_exit_status(self):
        for name, code in [('pt_submit_minimal', 0), ('ft_client_identity', 1)]:
            p = subprocess.run([sys.executable, str(c.ROOT / 'tools/validate_aw001_slice2c.py'), str(FIXTURES / (name + '.json'))], capture_output=True, text=True)
            self.assertEqual(p.returncode, code, p.stderr)
            self.assertEqual(json.loads(p.stdout)['is_valid'], code == 0)

    def test_simulation_only_and_no_external_execution(self):
        for key, value in [('mode', 'delegated'), ('execution_enabled', True), ('data_classification', 'real')]:
            f = fixture('pt_collective_approve'); f['server_context'][key] = value
            self.assert_code(f, 'ERR_SCHEMA_VALIDATION')

    def test_current_receipt_cannot_certify_an_unavailable_authority(self):
        f = fixture('pt_rejected_source_unavailable')
        f['server_context']['generated_auth_receipt']['verification_status'] = 'VERIFIED_ACTIVE'
        self.assert_code(f, 'ERR_RECEIPT_BINDING_MISMATCH')

    def test_projection_requires_collective_source_binding(self):
        d = fixture('pt_collective_approve')['after']['domain_objects']
        d['operations']['effectiveness_envelopes'][0]['effect_parameters_sha256'] = 'sha256:' + '0' * 64
        with self.assertRaises(c.ContractFailure) as caught:
            c.project_operations(d)
        self.assertEqual(caught.exception.report['error_code'], 'ERR_COLLECTIVE_PROJECTION_MISMATCH')


class ResponsibilityBoundaryTests(unittest.TestCase):
    def test_current_verifier_is_recused_when_appealing_an_earlier_authorization(self):
        d = fixture('pt_open_review')['before']['domain_objects']
        old = c.load_json(c.ROOT / 'fixtures/aw001_slice2/2b/pt_verify_phase1_complete.json')['after']['domain_objects']
        d['operations'] = copy.deepcopy(old)
        for env in old['effectiveness_envelopes']:
            if not any(r['envelope_id'] == env['envelope_id'] for r in d['historical_actor_bindings']):
                principal = env['auth_check_receipt']['authenticated_principal_id']
                d['historical_actor_bindings'].append({'envelope_id': env['envelope_id'], 'envelope_sha256': c.digest(env), 'principal_id': principal, 'member_id': principal.replace('principal-', 'member-'), 'status': 'VERIFIED', 'source_ref': d['case_policies'][0]['source_ref']})
        c.validate_domain(d)
        members, _ = c.recusal(d, d['appeals'][0], d['case_policies'][0]['sod_recusal_policy'])
        self.assertIn('member-verifier', members)

    def test_order_appeal_traces_original_issuer_and_only_restricted_executors(self):
        d = fixture('pt_submit_with_active_po')['after']['domain_objects']
        order = d['operations']['protective_orders'][0]
        appeal = d['appeals'][0]
        appeal.update(challenged_object_kind='PROTECTIVE_ORDER', challenged_object_id=order['order_id'], challenged_object_version_seq=order['version_seq'], challenged_decision_envelope_id=order['issuance_envelope_id'])
        c.validate_domain(d)
        members, trace = c.recusal(d, appeal, d['case_policies'][0]['sod_recusal_policy'])
        issuer = next(e for e in d['operations']['protection_envelopes'] if e['envelope_id'] == order['issuance_envelope_id'])['auth_check_receipt']['authenticated_principal_id'].replace('principal-', 'member-')
        self.assertIn(issuer, members)
        self.assertIn('member-operator', members)
        self.assertTrue(any(r['reason'] == 'ORIGINAL_ORDER_ISSUER' for r in trace))
        self.assertNotIn('member-proposer', members)

    def test_plain_2b_projection_is_not_a_complete_collective_archive(self):
        d = fixture('pt_collective_approve')['after']['domain_objects']
        projected = c.project_operations(d)
        self.assertTrue(c.b.DOMAIN_VALIDATOR.is_valid(projected))
        self.assertNotIn('procedure_envelopes', projected)
        self.assertEqual(len(d['procedure_envelopes'][0]['collective_member_attestations']), 3)

    def test_registry_rejects_unknown_action_and_relaxed_write_policy(self):
        import tempfile
        registry = c.load_json(c.SPEC / 'fixed-action-registry.json')
        for mutation in ['action', 'policy']:
            r = copy.deepcopy(registry)
            if mutation == 'action': r['actions']['BYPASS'] = r['actions'][c.ADJUDICATE]
            else: r['actions'][c.ADJUDICATE]['write_policy'] = 'ARBITRARY_WRITE'
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'registry.json'; p.write_text(json.dumps(r))
                self.assertEqual(c.check_registry(p)['error_code'], 'ERR_ACTION_REGISTRY_VIOLATION')
