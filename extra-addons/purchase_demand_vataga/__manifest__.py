{
    'name': 'Purchase Demand Vataga',
    'version': '1.4',
    'category': 'Inventory/Purchase',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'sale_demand_vataga',
    ],
    'data': [
        'views/product_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'purchase_demand_vataga/static/src/product_catalog/kanban_model.js',
            'purchase_demand_vataga/static/src/product_catalog/order_line/order_line.xml',
            'purchase_demand_vataga/static/src/product_catalog/order_line/order_line.js',
        ]
    },
}
