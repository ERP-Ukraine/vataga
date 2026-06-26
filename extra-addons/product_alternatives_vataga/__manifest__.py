{
    'name': 'Product Analogs',
    'version': '17.0.1.1',
    'category': 'Inventory',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'mrp',
        'product',
        'purchase_demand_vataga',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/product_template_views.xml',
        'views/product_analytic_views.xml',
        'views/mrp_bom_views.xml',
        'views/mrp_production_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_alternatives_vataga/static/src/views/pivot/analog_marker.xml',
            'product_alternatives_vataga/static/src/views/pivot/analog_marker.js',
            'product_alternatives_vataga/static/src/fields/product_analog_marker.xml',
            'product_alternatives_vataga/static/src/fields/product_analog_marker.js',
            'product_alternatives_vataga/static/src/fields/product_analog_marker.css',
            'product_alternatives_vataga/static/src/scss/analog_marker.scss',
        ],
    },
}
