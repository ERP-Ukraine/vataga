{
    'name': 'Закупочні затвердження Vataga',
    'version': '17.0.1.0.3',
    'category': 'Human Resources/Approvals',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'license': 'LGPL-3',
    'depends': ['approvals', 'approvals_purchase', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'wizard/approval_product_resolve_views.xml',
        'views/approval_request_views.xml',
        'views/purchase_request_registry_views.xml',
        'views/product_template_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'approvals_purchase_vataga/static/src/url_policy.js',
            'approvals_purchase_vataga/static/src/url_field.js',
            'approvals_purchase_vataga/static/src/calendar_date_field.js',
        ],
        'web.qunit_suite_tests': [
            'approvals_purchase_vataga/static/tests/readonly_resolve_tests.js',
        ],
    },
    'installable': True,
    'application': False,
}
