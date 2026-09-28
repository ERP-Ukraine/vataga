{
    'name': 'Контроль мінімальних залишків',
    'summary': 'Контроль мінімальних залишків товарів по складах та локаціях',
    'description': 'Налаштування мінімальних залишків і складський звіт лише для читання.',
    'author': 'ERP Ukraine LLC',
    'version': '17.0.1.0.4',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_warehouse_views.xml',
        'views/product_template_views.xml',
        'views/stock_minimum_report_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stock_minimum_control_vataga/static/src/stock_minimum_colors.js',
            'stock_minimum_control_vataga/static/src/stock_minimum_report.js',
            'stock_minimum_control_vataga/static/src/stock_minimum_report.xml',
            'stock_minimum_control_vataga/static/src/stock_minimum_report.scss',
        ],
        'web.qunit_suite_tests': [
            'stock_minimum_control_vataga/static/tests/stock_minimum_report_tests.js',
        ],
    },
    'installable': True,
    'application': False,
}
