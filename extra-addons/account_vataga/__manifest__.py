{
    'name' : 'Accounting Vataga',
    'version': '1.20',
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
        'data/mail_message_subtype.xml',
        'security/account_payment_unreconcile.xml',
        'data/account_payment_view.xml',
        'views/account_move.xml',
        'views/res_config_settings_views.xml',
        'views/account_payment_view.xml',
        'wizard/account_payment_register.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'account_vataga/static/src/chatter/invoice_chatter.js',
            'account_vataga/static/src/chatter/invoice_chatter.xml',
            'account_vataga/static/src/chatter/invoice_chatter.scss',
            'account_vataga/static/src/components/account_payment_unreconcile.xml',
        ],
    },
}
