{
    'name': 'Product and transaction labels',
    'version': '1.18',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'mail',
        'product',
        'stock',
        'l10n_ua_stock_reports',
        # Transfer/package labels are disabled.
    ],
    'data': [
        'data/mail_message_subtype.xml',
        'views/product_search_views.xml',
        'views/report_product_label_dymo.xml',
        'views/stock_picking_views.xml',
        'report/report_deliveryslip.xml',
        'report/report_picking.xml',
        # Transfer/package labels are disabled; keep only the product label override active.
        # 'report/stock_picking_transfer_label.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_vataga/static/src/chatter/product_chatter.js',
            'product_vataga/static/src/chatter/product_chatter.xml',
        ],
    },
}
