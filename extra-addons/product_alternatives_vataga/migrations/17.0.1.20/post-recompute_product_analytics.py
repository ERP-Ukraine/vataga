import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ProductAnalytic = env['product.analytic'].with_context(active_test=False)
    last_id = 0
    recomputed_count = 0
    while True:
        analytics = ProductAnalytic.search(
            [('id', '>', last_id)], order='id', limit=500,
        )
        if not analytics:
            break
        last_id = analytics[-1].id
        recomputed_count += analytics._recompute_analog_rollup_fields(batch_size=500)
        # Flush before clearing prefetch caches to bound memory across batches.
        # Keep the upgrade atomic; do not commit between batches.
        env.invalidate_all()
        _logger.info(
            'Product analytic stored rollup migration: recomputed %s rows through id %s',
            recomputed_count, last_id,
        )
    _logger.info(
        'Product analytic stored rollup migration complete: recomputed %s existing rows',
        recomputed_count,
    )
