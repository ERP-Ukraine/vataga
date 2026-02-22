from . import models, controllers

def post_init_hook(env):
    sale_order_lines = env['sale.order.line'].search([])
    sale_order_lines.set_bom_id()
    
