import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    result = env[
        'product.analytic'
    ]._backfill_analog_rollup_product_analytics(batch_size=500)
    _logger.info('Analog rollup product analytic migration result: %s', result)
