from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['account.bank.statement.line']._backfill_payment_invoice_references(
        [('is_reconciled', '=', True)]
    )
