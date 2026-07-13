{
    'name': 'Product and transaction labels',
    'version': '1.4',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'product',
        # Transfer/package labels are disabled.
        # 'stock',
    ],
    'data': [
        'views/report_product_label_dymo.xml',
        # Transfer/package labels are disabled; keep only the product label override active.
        # 'views/stock_picking_views.xml',
        # 'report/stock_picking_transfer_label.xml',
    ],
}
