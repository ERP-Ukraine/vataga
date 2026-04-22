from datetime import date

from odoo import Command
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStatementReferences(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        cls.vendor = cls.env["res.partner"].create({
            "name": "Test Vendor",
            "supplier_rank": 1,
        })

        company = cls.env.company

        if not company.account_journal_payment_credit_account_id:
            company.account_journal_payment_credit_account_id = cls.env["account.account"].create({
                "name": "Outstanding Payments",
                "code": "OUTPAY",
                "account_type": "asset_current",
                "reconcile": True,
            })

        if not company.account_journal_payment_debit_account_id:
            company.account_journal_payment_debit_account_id = cls.env["account.account"].create({
                "name": "Outstanding Receipts",
                "code": "OUTREC",
                "account_type": "asset_current",
                "reconcile": True,
            })

        cls.bank_account = cls.env["account.account"].create({
            "name": "Test Bank",
            "code": "BNKTSR",
            "account_type": "asset_cash",
        })
        cls.payable_account = cls.env["account.account"].create({
            "name": "Test Payable",
            "code": "TPAYR",
            "account_type": "liability_payable",
            "reconcile": True,
        })
        cls.expense_account = cls.env["account.account"].create({
            "name": "Test Expense",
            "code": "TEXPR",
            "account_type": "expense",
        })
        cls.vendor.property_account_payable_id = cls.payable_account

        cls.bank_journal = cls.env["account.journal"].create({
            "name": "Test Bank Journal Ref",
            "type": "bank",
            "code": "TBR",
            "default_account_id": cls.bank_account.id,
            "suspense_account_id": company.account_journal_payment_credit_account_id.id,
        })
        cls.direct_bill_bank_journal = cls.env["account.journal"].create({
            "name": "Direct Bill Bank Journal Ref",
            "type": "bank",
            "code": "DBR",
            "default_account_id": cls.bank_account.id,
            "suspense_account_id": cls.payable_account.id,
        })
        cls.purchase_journal = cls.env["account.journal"].search([
            ("type", "=", "purchase"),
            ("company_id", "=", company.id),
        ], limit=1)
        if not cls.purchase_journal:
            cls.purchase_journal = cls.env["account.journal"].create({
                "name": "Test Purchase Journal Ref",
                "type": "purchase",
                "code": "TPR",
                "company_id": company.id,
                "default_account_id": cls.expense_account.id,
            })

    def _create_vendor_bill(self, amount):
        vendor_bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.vendor.id,
            "journal_id": self.purchase_journal.id,
            "invoice_date": date(2025, 11, 1),
            "invoice_line_ids": [
                Command.create({
                    "name": "Test expense",
                    "quantity": 1,
                    "price_unit": amount,
                    "account_id": self.expense_account.id,
                })
            ],
        })
        vendor_bill.action_post()
        return vendor_bill

    def _create_payment_from_bill(self, bill):
        register = self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=bill.ids,
        ).create({
            "journal_id": self.bank_journal.id,
            "amount": bill.amount_total,
            "payment_date": date(2025, 11, 28),
        })
        payments = register._create_payments()
        self.assertEqual(len(payments), 1, "Expected a single payment for the bill")
        return payments

    def _create_statement_line(self, journal, stmt_date, amount, payment_ref):
        return self.env["account.bank.statement.line"].create({
            "payment_ref": payment_ref,
            "partner_id": self.vendor.id,
            "amount": amount,
            "date": stmt_date,
            "journal_id": journal.id,
        })

    def _get_statement_suspense_line(self, stmt_line):
        return stmt_line.move_id.line_ids.filtered(
            lambda line: line.account_id == stmt_line.journal_id.suspense_account_id
        )

    def _get_bill_payable_line(self, bill):
        return bill.line_ids.filtered(
            lambda line: line.account_id == self.payable_account
        )

    def _get_payment_payable_line(self, payment):
        return payment.move_id.line_ids.filtered(
            lambda line: line.account_id == self.payable_account
        )

    def _get_payment_outstanding_line(self, payment):
        return payment.move_id.line_ids.filtered(
            lambda line: line.account_id == payment.outstanding_account_id
        )

    def _reconcile_statement_with_move_line(self, move_line, stmt_line):
        stmt_aml = self._get_statement_suspense_line(stmt_line)
        self.assertTrue(stmt_aml, "Statement suspense line not found")
        self.assertEqual(
            move_line.account_id,
            stmt_aml.account_id,
            "Accounts must match for reconciliation",
        )
        (move_line | stmt_aml).reconcile()
        return stmt_line

    def _reconcile_payment_with_statement(self, payment, stmt_date, amount):
        stmt_line = self._create_statement_line(
            self.bank_journal,
            stmt_date,
            -amount,
            f"TEST/{payment.id}",
        )

        stmt_line.move_id.flush_recordset(['statement_line_id'])
        stmt_line.move_id.line_ids.flush_recordset(['statement_line_id'])

        payment_line = self._get_payment_outstanding_line(payment)
        stmt_aml = self._get_statement_suspense_line(stmt_line)

        self.assertTrue(payment_line, "Payment outstanding line not found")
        self.assertTrue(stmt_aml, "Statement suspense line not found")
        self.assertEqual(
            payment_line.account_id,
            stmt_aml.account_id,
            "Accounts must match for reconciliation",
        )

        (payment_line | stmt_aml).reconcile()
        return stmt_line

    def test_statement_refs_set_from_payment_and_bill(self):
        bill = self._create_vendor_bill(1000.0)
        payment = self._create_payment_from_bill(bill)

        stmt_line = self._reconcile_payment_with_statement(
            payment, date(2025, 11, 28), 1000.0
        )
        stmt_line.invalidate_recordset(['payment_reference', 'invoice_reference'])

        self.assertEqual(stmt_line.payment_reference, payment.name)
        self.assertEqual(stmt_line.invoice_reference, bill.name)

    def test_statement_invoice_ref_cleared_when_payment_unlinked_from_bill(self):
        bill = self._create_vendor_bill(600.0)
        payment = self._create_payment_from_bill(bill)
        stmt_line = self._reconcile_payment_with_statement(
            payment, date(2025, 11, 28), 600.0
        )

        payment_payable_line = self._get_payment_payable_line(payment)
        bill_payable_line = self._get_bill_payable_line(bill)
        self.assertTrue(payment_payable_line, "Payment payable line not found")
        self.assertTrue(bill_payable_line, "Bill payable line not found")

        (payment_payable_line | bill_payable_line).remove_move_reconcile()
        stmt_line.invalidate_recordset(['payment_reference', 'invoice_reference'])

        self.assertEqual(stmt_line.payment_reference, payment.name)
        self.assertFalse(stmt_line.invoice_reference)

    def test_statement_refs_cleared_when_payment_unlinked_from_statement(self):
        bill = self._create_vendor_bill(800.0)
        payment = self._create_payment_from_bill(bill)
        stmt_line = self._reconcile_payment_with_statement(
            payment, date(2025, 11, 28), 800.0
        )

        payment_outstanding_line = self._get_payment_outstanding_line(payment)
        stmt_aml = self._get_statement_suspense_line(stmt_line)
        self.assertTrue(payment_outstanding_line, "Payment outstanding line not found")
        self.assertTrue(stmt_aml, "Statement suspense line not found")

        (payment_outstanding_line | stmt_aml).remove_move_reconcile()
        stmt_line.invalidate_recordset(['payment_reference', 'invoice_reference'])

        self.assertFalse(stmt_line.payment_reference)
        self.assertFalse(stmt_line.invoice_reference)

    def test_statement_invoice_ref_set_for_direct_bill_reconcile(self):
        bill = self._create_vendor_bill(700.0)
        stmt_line = self._create_statement_line(
            self.direct_bill_bank_journal,
            date(2025, 11, 28),
            -700.0,
            bill.name,
        )
        bill_payable_line = self._get_bill_payable_line(bill)
        self.assertTrue(bill_payable_line, "Bill payable line not found")

        self._reconcile_statement_with_move_line(bill_payable_line, stmt_line)
        stmt_line.invalidate_recordset(['payment_reference', 'invoice_reference'])

        self.assertFalse(stmt_line.payment_reference)
        self.assertEqual(stmt_line.invoice_reference, bill.name)

        stmt_aml = self._get_statement_suspense_line(stmt_line)
        (bill_payable_line | stmt_aml).remove_move_reconcile()
        stmt_line.invalidate_recordset(['payment_reference', 'invoice_reference'])

        self.assertFalse(stmt_line.payment_reference)
        self.assertFalse(stmt_line.invoice_reference)

    def test_standard_payment_ref_is_not_changed(self):
        bill = self._create_vendor_bill(900.0)
        payment = self._create_payment_from_bill(bill)
        original_reference = f"TEST/{payment.id}"
        stmt_line = self._reconcile_payment_with_statement(
            payment, date(2025, 11, 28), 900.0
        )
        stmt_line.invalidate_recordset([
            'payment_ref',
            'payment_reference',
            'invoice_reference',
        ])

        self.assertEqual(stmt_line.payment_ref, original_reference)
        self.assertEqual(stmt_line.payment_reference, payment.name)
        self.assertEqual(stmt_line.invoice_reference, bill.name)

    def test_backfill_payment_invoice_references(self):
        bill = self._create_vendor_bill(1100.0)
        payment = self._create_payment_from_bill(bill)
        stmt_line = self._reconcile_payment_with_statement(
            payment, date(2025, 11, 28), 1100.0
        )
        original_reference = stmt_line.payment_ref

        stmt_line.write({
            'payment_reference': False,
            'invoice_reference': False,
        })

        self.env['account.bank.statement.line']._backfill_payment_invoice_references(
            [('id', '=', stmt_line.id)]
        )
        stmt_line.invalidate_recordset([
            'payment_ref',
            'payment_reference',
            'invoice_reference',
        ])

        self.assertEqual(stmt_line.payment_ref, original_reference)
        self.assertEqual(stmt_line.payment_reference, payment.name)
        self.assertEqual(stmt_line.invoice_reference, bill.name)
