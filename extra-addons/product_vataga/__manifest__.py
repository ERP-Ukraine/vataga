{
    'name': 'Product and transaction labels',
    'version': '1.0',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'product',
        'stock',
    ],
    'data': [
        'views/report_product_label_dymo.xml',
        'views/stock_picking_views.xml',
        'report/stock_picking_transfer_label.xml',
    ],
}
