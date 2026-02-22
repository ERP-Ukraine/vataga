{
    'name' : 'Accounting Vataga',
    'version': '1.10',
    'category': 'Accounting/Accounting',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'account',
        'analytic_vataga',
    ],
    'data': [
        'data/account_payment_view.xml',
        'views/account_move.xml',
        'views/res_config_settings_views.xml',
        'views/account_payment_view.xml',
        'wizard/account_payment_register.xml',
    ],
    'assets': {},
}
