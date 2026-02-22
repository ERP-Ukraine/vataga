from odoo.tests.common import tagged, TransactionCase
from datetime import date


@tagged("post_install", "-at_install")
class TestBankPaymentDate(TransactionCase):

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
            "code": "BNKTEST",
            "account_type": "asset_cash",
        })

        cls.bank_journal = cls.env["account.journal"].create({
            "name": "Test Bank Journal",
            "type": "bank",
            "code": "TBJ",
            "default_account_id": cls.bank_account.id,
            "suspense_account_id": company.account_journal_payment_credit_account_id.id,
        })

    def _create_payment(self, amount):
        payment = self.env["account.payment"].create({
            "payment_type": "outbound",
            "partner_type": "supplier",
            "partner_id": self.vendor.id,
            "amount": amount,
            "journal_id": self.bank_journal.id,
        })
        payment.action_post()
        return payment

    def _reconcile_payment_with_statement(self, payment, stmt_date, amount):
        stmt_line = self.env["account.bank.statement.line"].create({
            "payment_ref": f"TEST/{payment.id}",
            "partner_id": self.vendor.id,
            "amount": -amount,
            "date": stmt_date,
            "journal_id": self.bank_journal.id,
        })

        stmt_line.move_id.flush_recordset(['statement_line_id'])
        stmt_line.move_id.line_ids.flush_recordset(['statement_line_id'])

        payment_line = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id
        )

        stmt_aml = stmt_line.move_id.line_ids.filtered(
            lambda l: l.account_id == self.bank_journal.suspense_account_id
        )

        self.assertTrue(payment_line, "Payment outstanding line not found")
        self.assertTrue(stmt_aml, "Statement suspense line not found")
        self.assertEqual(
            payment_line.account_id,
            stmt_aml.account_id,
            "Accounts must match for reconciliation"
        )

        (payment_line | stmt_aml).reconcile()

        self.assertTrue(
            payment_line.matched_debit_ids or payment_line.matched_credit_ids,
            "Reconciliation did not create partial reconcile records"
        )

        return stmt_line

    def test_no_date_if_not_reconciled(self):
        payment = self._create_payment(1000.0)
        self.assertFalse(payment.bank_payment_date)

    def test_date_set_after_reconcile(self):
        payment = self._create_payment(1000.0)
        test_date = date(2025, 11, 28)

        self._reconcile_payment_with_statement(payment, test_date, 1000.0)

        payment.invalidate_recordset(['reconciled_statement_line_ids', 'bank_payment_date'])

        self.assertEqual(payment.bank_payment_date, test_date)

    def test_date_cleared_after_unreconcile(self):
        payment = self._create_payment(500.0)

        self._reconcile_payment_with_statement(payment, date(2025, 11, 29), 500.0)
        payment.invalidate_recordset(['reconciled_statement_line_ids', 'bank_payment_date'])

        self.assertTrue(payment.bank_payment_date)

        payment.move_id.line_ids.remove_move_reconcile()
        payment.invalidate_recordset(['reconciled_statement_line_ids', 'bank_payment_date'])

        self.assertFalse(payment.bank_payment_date)

    def test_date_max_for_multiple_statements(self):
        payment = self._create_payment(1500.0)

        self._reconcile_payment_with_statement(payment, date(2025, 11, 25), 500.0)
        self._reconcile_payment_with_statement(payment, date(2025, 11, 27), 1000.0)

        payment.invalidate_recordset(['reconciled_statement_line_ids', 'bank_payment_date'])

        self.assertEqual(payment.bank_payment_date, date(2025, 11, 27))
