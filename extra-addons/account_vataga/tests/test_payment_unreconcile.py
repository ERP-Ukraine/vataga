from unittest.mock import patch

from odoo import Command
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


GROUP_XMLID = 'account_vataga.group_account_payment_unreconcile'
MODERATOR_XMLID = '__custom__.user_group_for_moderation'


@tagged('post_install', '-at_install')
class TestPaymentUnreconcile(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unreconcile_group = cls.env.ref(GROUP_XMLID)
        cls.accountant_group = cls.env.ref('account.group_account_user')
        company = cls.company_data['company']
        cls.accountant = cls.env['res.users'].create({
            'name': 'Payment unreconcile test accountant',
            'login': 'payment_unreconcile_test_accountant',
            'company_id': company.id,
            'company_ids': [Command.set(company.ids)],
            # Replace default user groups, which may include Accounting Manager.
            'groups_id': [Command.set(cls.accountant_group.ids)],
        })

    def _reconciled_invoice(self):
        invoice = self.init_invoice(
            'out_invoice', amounts=[100.0], taxes=[], post=True
        )
        payment = self.init_payment(100.0, post=True)
        lines = (invoice.line_ids | payment.move_id.line_ids).filtered(
            lambda line: line.account_id.account_type == 'asset_receivable'
        )
        self.assertEqual(len(lines), 2)
        lines.reconcile()
        partial = lines.matched_debit_ids | lines.matched_credit_ids
        self.assertEqual(len(partial), 1)
        self.assertEqual(invoice.amount_residual, 0.0)
        return invoice, lines, partial

    def test_accountant_without_permission_cannot_unreconcile(self):
        self.assertTrue(self.accountant.has_group('account.group_account_user'))
        self.assertFalse(self.accountant.has_group(GROUP_XMLID))
        invoice, lines, partial = self._reconciled_invoice()
        with self.assertRaises(AccessError):
            invoice.with_user(self.accountant).js_remove_outstanding_partial(partial.id)
        self.assertTrue(partial.exists())
        self.assertTrue(all(lines.mapped('reconciled')))
        self.assertEqual(invoice.amount_residual, 0.0)

    def test_accountant_with_permission_can_unreconcile(self):
        self.accountant.write({
            'groups_id': [Command.link(self.unreconcile_group.id)],
        })
        self.assertTrue(self.accountant.has_group(GROUP_XMLID))
        invoice, lines, partial = self._reconciled_invoice()
        invoice.with_user(self.accountant).js_remove_outstanding_partial(partial.id)
        self.assertFalse(partial.exists())
        self.assertFalse(any(lines.mapped('reconciled')))
        self.assertEqual(invoice.amount_residual, 100.0)

    def test_existing_moderator_inherits_permission(self):
        moderator = self.env.ref(MODERATOR_XMLID, raise_if_not_found=False)
        if not moderator:
            self.skipTest('This database has no UI-created moderator External ID')
        self.assertIn(self.unreconcile_group, moderator.implied_ids)
        self.assertIn(self.accountant_group, moderator.implied_ids)
        self.accountant.write({'groups_id': [Command.set(moderator.ids)]})
        self.assertTrue(self.accountant.has_group(GROUP_XMLID))

    def test_manager_inherits_permission_through_existing_moderator(self):
        moderator = self.env.ref(MODERATOR_XMLID, raise_if_not_found=False)
        if not moderator:
            self.skipTest('This database has no UI-created moderator External ID')
        manager = self.env.ref('account.group_account_manager')
        if moderator not in manager.trans_implied_ids:
            self.skipTest('This database has no Manager -> Moderator inheritance')
        self.assertIn(self.unreconcile_group, manager.trans_implied_ids)
        self.accountant.write({'groups_id': [Command.set(manager.ids)]})
        self.assertTrue(self.accountant.has_group(GROUP_XMLID))

    def test_link_preserves_existing_groups_and_is_idempotent(self):
        # An isolated fixture, never registered under the production XML ID.
        fixture = self.env['res.groups'].create({
            'name': 'Payment permission inheritance test fixture',
            'implied_ids': [Command.link(self.accountant_group.id)],
        })
        self.accountant.write({'groups_id': [Command.link(fixture.id)]})
        original_ref = type(self.env).ref

        def resolve(env, xmlid, *args, **kwargs):
            if xmlid == MODERATOR_XMLID:
                return fixture
            return original_ref(env, xmlid, *args, **kwargs)

        before = fixture.implied_ids
        with patch.object(type(self.env), 'ref', resolve):
            self.env['res.groups']._link_payment_unreconcile_group()
            self.env['res.groups']._link_payment_unreconcile_group()
        self.assertEqual(
            set(fixture.implied_ids.ids), set((before | self.unreconcile_group).ids)
        )
        self.assertIn(self.accountant_group, fixture.implied_ids)
        self.assertTrue(self.accountant.has_group(GROUP_XMLID))

    def test_missing_moderator_does_not_create_group(self):
        original_ref = type(self.env).ref

        def resolve(env, xmlid, *args, **kwargs):
            if xmlid == MODERATOR_XMLID:
                return None
            return original_ref(env, xmlid, *args, **kwargs)

        groups = self.env['res.groups'].search([])
        with patch.object(type(self.env), 'ref', resolve):
            with self.assertLogs(
                'odoo.addons.account_vataga.models.res_groups', level='WARNING'
            ):
                self.env['res.groups']._link_payment_unreconcile_group()
        self.assertEqual(self.env['res.groups'].search([]), groups)
