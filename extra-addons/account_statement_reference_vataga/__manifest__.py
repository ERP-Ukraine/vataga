{
    'name': 'Account Statement Reference Vataga',
    'version': '1.1',
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
        'account_accountant',
    ],
    'data': [
        'views/account_statement_reference_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'assets': {},
}
