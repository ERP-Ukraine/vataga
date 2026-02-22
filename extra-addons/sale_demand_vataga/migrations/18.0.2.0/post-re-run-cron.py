from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['mrp.bom'].search([]).need_update_to_purchase = True
    env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()
