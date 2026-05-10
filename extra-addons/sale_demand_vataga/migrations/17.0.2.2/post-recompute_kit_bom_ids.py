from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    result = env['product.analytic']._recompute_stale_kit_bom_ids()
    env['ir.config_parameter'].sudo().set_param(
        'demand_diag.kit_bom_migration_result',
        '\n'.join(
            [
                '[DEMAND_KIT_BOM_MIGRATION] checked=%s' % result['checked'],
                '[DEMAND_KIT_BOM_MIGRATION] mismatch_count=%s'
                % result['mismatch_count'],
                '[DEMAND_KIT_BOM_MIGRATION] updated_count=%s'
                % result['updated_count'],
                '[DEMAND_KIT_BOM_MIGRATION] remaining_mismatch_count=%s'
                % result['remaining_mismatch_count'],
            ]
        ),
    )
