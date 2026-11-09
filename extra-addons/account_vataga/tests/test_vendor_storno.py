from lxml import etree

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestVendorStorno(AccountTestInvoicingCommon):
    @classmethod
    def setup_company_data(cls, company_name, **kwargs):
        # Standard test fixtures use underscores, forbidden by this project's partner validation.
        return super().setup_company_data(company_name.replace('_', '-'), **kwargs)

    def _purchase_line(self):
        return self.env['purchase.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [Command.create({
                'name': 'Storno test product', 'product_id': self.product_a.id,
                'product_qty': 1, 'product_uom': self.product_a.uom_po_id.id,
                'price_unit': 100, 'date_planned': fields.Datetime.now(),
            })],
        }).order_line

    def _vendor_move(self, move_type='in_invoice', purchase_line=None, **extra):
        purchase_lines = purchase_line or [None]
        return self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner_a.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'product_id': self.product_a.id, 'price_unit': 100,
                'tax_ids': [Command.clear()],
                'purchase_line_id': line.id if line else False,
            }) for line in purchase_lines],
            **extra,
        })

    def _payable_lines(self, move):
        return move.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable')

    def _register_payment(self, move):
        return self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=move.ids,
        ).create({
            'journal_id': self.company_data['default_journal_bank'].id,
            # Enter purposes explicitly so the fixture also works without the ERP UA contract addons.
            'purpose_dcu': 'Storno integration test', 'purpose_pumb': 'Storno integration test',
        })._create_payments()

    def _bank_match(self, lines, amount, journal=None):
        statement = self.env['account.bank.statement.line'].create({
            'payment_ref': 'Storno integration test',
            'partner_id': self.partner_a.id,
            'amount': amount,
            'journal_id': (journal or self.company_data['default_journal_bank']).id,
        })
        # Use the same ORM reconciliation as the bank widget, without requiring Enterprise UI.
        _, suspense, _ = statement._seek_for_lines()
        suspense.with_context(skip_account_move_synchronization=True).account_id = lines.account_id
        (lines | suspense).reconcile()
        return suspense

    def _assert_states(self, move, payment, display=None):
        self.assertEqual(move.payment_state, payment)
        self.assertEqual(move.vataga_storno_payment_display, display or payment)

    def test_paid_vendor_bills_with_same_purchase_and_one_refund(self):
        order_line = self._purchase_line()
        bill_a = self._vendor_move(purchase_line=order_line)
        bill_b = self._vendor_move(purchase_line=order_line)
        refund = self._vendor_move('in_refund', purchase_line=order_line)
        self.assertFalse(refund.vataga_storno_bill_id)
        (bill_a | bill_b | refund).action_post()
        self._assert_states(bill_a, 'not_paid')
        refund.vataga_storno_bill_id = bill_a
        self._assert_states(bill_a, 'not_paid')

        payment = self._register_payment(bill_a)
        liquidity = payment._seek_for_lines()[0]
        matched_payment_line = self._bank_match(liquidity, -100)
        self._bank_match(self._payable_lines(bill_b), -100)
        self._assert_states(bill_a, 'paid')  # Refund still unpaid.
        refund.vataga_storno_bill_id = False
        self._bank_match(self._payable_lines(refund), 100)
        self._assert_states(bill_a, 'paid')  # Both paid but no link.

        refund.vataga_storno_bill_id = bill_a
        self._assert_states(bill_a, 'paid', 'storno')
        self._assert_states(bill_b, 'paid')
        self._assert_states(refund, 'paid')

        matched_payment_line.remove_move_reconcile()
        # Community can still report 'paid' after removing just the payment-to-bank link.
        self.assertIn(bill_a.payment_state, ('paid', 'in_payment'))
        self.assertEqual(bill_a.vataga_storno_payment_display, bill_a.payment_state)
        (liquidity | matched_payment_line).reconcile()
        self._assert_states(bill_a, 'paid', 'storno')

        refund.vataga_storno_bill_id = bill_b
        self._assert_states(bill_a, 'paid')
        self._assert_states(bill_b, 'paid', 'storno')
        refund.vataga_storno_bill_id = False
        self._assert_states(bill_b, 'paid')

        refund.vataga_storno_bill_id = bill_b
        refund.button_draft()
        self._assert_states(bill_b, 'paid')
        refund.button_cancel()
        self._assert_states(bill_b, 'paid')
        self.assertEqual(refund.vataga_storno_bill_id, bill_b)

    def test_paid_without_bank_statement(self):
        # Using the bank account itself as outstanding sets is_matched without a statement.
        journal = self.company_data['default_journal_bank']
        methods = journal.outbound_payment_method_line_ids | journal.inbound_payment_method_line_ids
        methods.payment_account_id = journal.default_account_id
        bill = self._vendor_move()
        refund = self._vendor_move('in_refund', vataga_storno_bill_id=bill.id)
        (bill | refund).action_post()
        self.assertTrue(self._register_payment(bill).is_matched)
        self.assertTrue(self._register_payment(refund).is_matched)
        self._assert_states(bill, 'paid')
        self._assert_states(refund, 'paid')

    def test_paid_refund_without_paid_bill_and_cash_is_not_bank(self):
        bill = self._vendor_move()
        refund = self._vendor_move('in_refund', vataga_storno_bill_id=bill.id)
        (bill | refund).action_post()
        self._bank_match(self._payable_lines(refund), 100)
        self._assert_states(refund, 'paid')
        self._assert_states(bill, 'not_paid')
        cash = self.env['account.journal'].create({'name': 'Cash storno', 'type': 'cash', 'code': 'CSSTR'})
        self._bank_match(self._payable_lines(bill), -100, journal=cash)
        self._assert_states(bill, 'paid')

    def test_wizard_sets_each_link_and_preserves_standard_reversed_status(self):
        line = self._purchase_line()
        bills = self._vendor_move(purchase_line=line) | self._vendor_move()
        bills.action_post()
        wizard = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=bills.ids,
        ).create({'journal_id': bills.journal_id.id})
        wizard.refund_moves()
        self.assertEqual(wizard.new_move_ids.vataga_storno_bill_id, bills)
        for refund in wizard.new_move_ids:
            self.assertEqual(refund.move_type, 'in_refund')
            self.assertEqual(refund.vataga_storno_bill_id, refund.reversed_entry_id)
            refund.action_post()
            bill = refund.reversed_entry_id
            # Some versions reconcile on posting via reversed_entry_id.
            open_terms = (self._payable_lines(bill) | self._payable_lines(refund)).filtered(
                lambda line: not line.reconciled
            )
            if open_terms:
                open_terms.reconcile()
            self._assert_states(bill, 'reversed')

    def test_link_server_validation_and_multiple_purchase_orders(self):
        orders = self._purchase_line() | self._purchase_line()
        bill = self._vendor_move(purchase_line=orders[1])
        wrong_order = self._vendor_move(purchase_line=self._purchase_line())
        wrong_vendor = self._vendor_move(purchase_line=orders[1], partner_id=self.partner_b.id)
        cancelled = self._vendor_move(purchase_line=orders[1])
        cancelled.button_cancel()
        refund = self._vendor_move('in_refund', purchase_line=orders)
        self.assertEqual(refund.vataga_storno_purchase_ids, orders.order_id)
        refund.vataga_storno_bill_id = bill
        for target in (wrong_order, wrong_vendor, cancelled, refund):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                refund.vataga_storno_bill_id = target
        with self.assertRaises(ValidationError), self.cr.savepoint():
            bill.vataga_storno_bill_id = bill
        with self.assertRaises(ValidationError), self.cr.savepoint():
            refund.partner_id = self.partner_b

        company = self.company_data_2
        foreign_bill = self.env['account.move'].with_company(company['company']).create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id,
            'journal_id': company['default_journal_purchase'].id,
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            refund.vataga_storno_bill_id = foreign_bill

        contact = self.env['res.partner'].create({'name': 'Vendor contact', 'parent_id': self.partner_a.id})
        no_purchase = self._vendor_move('in_refund', partner_id=contact.id, vataga_storno_bill_id=bill.id)
        self.assertFalse(no_purchase.vataga_storno_purchase_ids)
        self.assertEqual(no_purchase.vataga_storno_bill_id, bill)

    def test_one_qualifying_partial_amount_refund_is_enough(self):
        bill = self._vendor_move()
        first = self._vendor_move('in_refund', vataga_storno_bill_id=bill.id)
        second = self._vendor_move('in_refund', vataga_storno_bill_id=bill.id)
        first.invoice_line_ids.price_unit = 40
        (bill | first | second).action_post()
        self._bank_match(self._payable_lines(bill), -100)
        self._bank_match(self._payable_lines(first), 40)
        self._assert_states(second, 'not_paid')
        self._assert_states(bill, 'paid', 'storno')

    def test_form_domain_tracks_vendor_and_purchase_lines(self):
        moves = self.env['account.move']
        form = etree.fromstring(moves.get_view(self.env.ref('account.view_move_form').id)['arch'])
        domain_expression = form.xpath("//field[@name='vataga_storno_bill_id']")[0].get('domain')
        order_line = self._purchase_line()
        same_order = self._vendor_move(purchase_line=order_line)
        same_vendor = self._vendor_move()
        other_vendor = self._vendor_move(partner_id=self.partner_b.id)
        refund = self._vendor_move('in_refund')

        def candidates():
            domain = safe_eval(domain_expression, {
                'id': refund.id,
                'company_id': refund.company_id.id,
                'commercial_partner_id': refund.commercial_partner_id.id,
                'vataga_storno_purchase_ids': refund.vataga_storno_purchase_ids.ids,
            })
            return moves.search(domain)

        self.assertEqual(candidates(), same_order | same_vendor)
        refund.invoice_line_ids.purchase_line_id = order_line
        self.assertEqual(candidates(), same_order)
        refund.invoice_line_ids.purchase_line_id = False
        refund.partner_id = self.partner_b
        self.assertEqual(candidates(), other_vendor)

    def test_bank_matches_are_read_in_batches(self):
        bills = self.env['account.move']
        for _ in range(10):
            bill = self._vendor_move()
            refund = self._vendor_move('in_refund', vataga_storno_bill_id=bill.id)
            (bill | refund).action_post()
            payment = self._register_payment(bill)
            self._bank_match(payment._seek_for_lines()[0], -100)
            self._bank_match(self._payable_lines(refund), 100)
            bills |= bill

        def read_statuses(records):
            self.env.invalidate_all()
            before = self.cr.sql_log_count
            result = records.read(['vataga_storno_payment_display'])
            self.assertEqual({row['vataga_storno_payment_display'] for row in result}, {'storno'})
            return self.cr.sql_log_count - before

        one_bill_queries = read_statuses(bills.browse(bills.ids[:1]))
        # Let ORM fetch larger prefetch sets while keeping the number of queries bounded.
        self.assertLessEqual(read_statuses(bills), one_bill_queries + 4)
