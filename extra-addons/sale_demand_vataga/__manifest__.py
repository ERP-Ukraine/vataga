{
    'name': 'Sale Demand Vataga',
    'version': '2.11',
    'category': 'Sales/Sales',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'mrp',
        'account_vataga',
        'l10n_ua_contract_sale',
        'l10n_ua_contract_purchase',
        'purchase_vataga',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/product_views.xml',
        'views/account_analytic_account_views.xml',
        'views/sale_order_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'assets': {
        'web.assets_backend': [
            'sale_demand_vataga/static/src/views/pivot/pivot_renderer.xml',
            'sale_demand_vataga/static/src/views/pivot/pivot_renderer.js',
            'sale_demand_vataga/static/src/views/pivot/pivot_renderer.scss',
            'sale_demand_vataga/static/src/views/pivot/pivot_view.js',
        ],
        'web.qunit_suite_tests': [
            'sale_demand_vataga/static/tests/pivot_renderer_tests.js',
            'sale_demand_vataga/static/tests/pivot_view_tests.js',
        ],
    },
}
