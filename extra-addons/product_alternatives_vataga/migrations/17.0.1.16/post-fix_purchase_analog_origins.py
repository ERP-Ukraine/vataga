import logging

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    repaired = env['account.move.line']._backfill_purchase_analog_original_products()
    logging.getLogger(__name__).info('Repaired %s purchase analog invoice lines', repaired)
