{
    'name': 'Product and transaction labels',
    'version': '1.11',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'product',
        'stock',
        'l10n_ua_stock_reports',
        # Transfer/package labels are disabled.
    ],
    'data': [
        'views/product_search_views.xml',
        'views/report_product_label_dymo.xml',
        'report/report_deliveryslip.xml',
        # Transfer/package labels are disabled; keep only the product label override active.
        # 'views/stock_picking_views.xml',
        # 'report/stock_picking_transfer_label.xml',
    ],
}
