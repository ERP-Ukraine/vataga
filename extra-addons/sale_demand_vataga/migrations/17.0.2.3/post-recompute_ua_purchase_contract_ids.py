from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    result = env['product.analytic']._recompute_stale_ua_purchase_contract_ids()
    env['ir.config_parameter'].sudo().set_param(
        'demand_diag.ua_purchase_contract_migration_result',
        '\n'.join(
            [
                '[DEMAND_UA_PURCHASE_CONTRACT_MIGRATION] checked=%s'
                % result['checked'],
                '[DEMAND_UA_PURCHASE_CONTRACT_MIGRATION] mismatch_count=%s'
                % result['mismatch_count'],
                '[DEMAND_UA_PURCHASE_CONTRACT_MIGRATION] updated_count=%s'
                % result['updated_count'],
                '[DEMAND_UA_PURCHASE_CONTRACT_MIGRATION] remaining_mismatch_count=%s'
                % result['remaining_mismatch_count'],
            ]
        ),
    )
