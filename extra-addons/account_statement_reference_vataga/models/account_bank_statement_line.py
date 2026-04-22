from odoo import api, fields, models


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    payment_reference = fields.Char(
        string='Референс платежу',
        readonly=True,
        copy=False,
        oldname='linked_payment_ref',
    )
    invoice_reference = fields.Char(
        string='Референс рахунку',
        readonly=True,
        copy=False,
        oldname='linked_invoice_ref',
    )

    @staticmethod
    def _join_reference_names(records):
        names = [name for name in records.mapped('name') if name]
        return ', '.join(dict.fromkeys(names)) or False

    def _get_reconciled_counterpart_lines(self):
        self.ensure_one()
        _liquidity_lines, suspense_lines, _other_lines = self._seek_for_lines()
        return (
            suspense_lines.mapped('matched_debit_ids.debit_move_id')
            + suspense_lines.mapped('matched_credit_ids.credit_move_id')
        )

    def _get_payment_invoice_sources(self):
        self.ensure_one()
        counterpart_lines = self._get_reconciled_counterpart_lines()
        payments = counterpart_lines.move_id.payment_id | self.payment_ids
        direct_bills = counterpart_lines.move_id.filtered(
            lambda move: move.is_purchase_document(include_receipts=True)
        )
        payment_bills = payments.reconciled_bill_ids.filtered(
            lambda move: move.is_purchase_document(include_receipts=True)
        )
        return payments, direct_bills | payment_bills

    def _get_payment_invoice_reference_values(self):
        self.ensure_one()
        payments, bills = self._get_payment_invoice_sources()
        return {
            'payment_reference': self._join_reference_names(payments),
            'invoice_reference': self._join_reference_names(bills),
        }

    def _sync_payment_invoice_references(self):
        for st_line in self:
            values = st_line._get_payment_invoice_reference_values()
            if (
                st_line.payment_reference != values['payment_reference']
                or st_line.invoice_reference != values['invoice_reference']
            ):
                st_line.write(values)

    @api.model
    def _backfill_payment_invoice_references(self, domain=None, batch_size=1000):
        base_domain = list(domain or [])
        last_id = 0
        while True:
            lines = self.search(
                base_domain + [('id', '>', last_id)],
                order='id',
                limit=batch_size,
            )
            if not lines:
                break
            lines._sync_payment_invoice_references()
            last_id = lines[-1].id
