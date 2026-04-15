{
    'name': 'Product and transaction labels',
    'version': '1.0',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': True,
    'depends': [
        'product',
        'stock',
    ],
    'data': [
        'views/report_product_label_dymo.xml',
        'report/stock_picking_transfer_label.xml',
    ],
}
