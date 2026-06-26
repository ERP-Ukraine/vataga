def migrate(env, version):
    domain = [
        ('sale_contract_id', '!=', False),
        ('product_analytic_id', '=', False),
        ('state', '=', 'sale'),
    ]
    SaleOrderLinePurchase = env['sale.order.line.purchase']
    while True:
        lines = SaleOrderLinePurchase.search(domain, limit=500)
        if not lines:
            break
        lines._sync_product_analytic_id()
        lines.flush_recordset(['product_analytic_id'])
