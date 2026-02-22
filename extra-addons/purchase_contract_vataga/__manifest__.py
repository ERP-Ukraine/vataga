{
    'name' : 'Purchase Contracts Vataga',
    'version': '1.2',
    'category': 'Purchase with Sale Contracts',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'l10n_ua_contract_account',
        'purchase_vataga',
    ],
    'data': [
        'views/purchase_order.xml',
        'views/account_move.xml'
    ],
    'assets': {},
}
